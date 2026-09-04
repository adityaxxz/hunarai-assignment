"""Client behaviour that would actually break us, exercised over httpx's
MockTransport so nothing here touches the network."""

import httpx
import pytest

from app.integrations.hunar.client import HunarClient
from app.integrations.hunar.errors import (
    HunarAuthError,
    HunarBadRequestError,
    HunarQuotaError,
    HunarServerError,
    HunarValidationError,
)


def make_client(handler, **kwargs) -> HunarClient:
    return HunarClient(
        "unused-in-tests",
        base_url="https://api.voice.hunar.ai/external/v1",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
        **kwargs,
    )


def error_body(message: str, details: list[dict[str, str]] | None = None) -> dict:
    return {"success": False, "message": message, "details": details or []}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, HunarAuthError),
        (402, HunarQuotaError),
        (400, HunarBadRequestError),
    ],
)
async def test_status_codes_map_to_typed_errors(status: int, expected: type[Exception]) -> None:
    client = make_client(lambda request: httpx.Response(status, json=error_body("nope")))

    with pytest.raises(expected) as exc_info:
        await client.list_agents()

    assert exc_info.value.status_code == status
    assert exc_info.value.message == "nope"
    await client.aclose()


async def test_422_preserves_field_errors() -> None:
    body = error_body(
        "Validation Failed for fields: agent_id, callee_name",
        [
            {"field_name": "agent_id", "error_msg": "This field is required."},
            {"field_name": "callee_name", "error_msg": "This field is required."},
        ],
    )
    client = make_client(lambda request: httpx.Response(422, json=body))

    with pytest.raises(HunarValidationError) as exc_info:
        await client.get_call("call-1")

    assert exc_info.value.field_errors == {
        "agent_id": "This field is required.",
        "callee_name": "This field is required.",
    }
    await client.aclose()


async def test_500_is_retried_then_raises_but_400_is_not() -> None:
    attempts: list[int] = []

    def server_error(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500, json=error_body("boom"))

    client = make_client(server_error, max_attempts=3)
    with pytest.raises(HunarServerError):
        await client.list_agents()
    assert len(attempts) == 3, "5xx should be retried up to max_attempts"
    await client.aclose()

    attempts.clear()

    def bad_request(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, json=error_body("bad number"))

    client = make_client(bad_request, max_attempts=3)
    with pytest.raises(HunarBadRequestError):
        await client.list_agents()
    assert len(attempts) == 1, "4xx must never be retried"
    await client.aclose()


async def test_unknown_status_value_still_parses() -> None:
    """Hunar adding a status value must not break call ingestion."""
    body = {
        "id": "call-1",
        "status": "VOICEMAIL_DROPPED",
        "lifecycle_status": "SOMETHING_NEW",
        "engagement_status": "PARTIALLY_ENGAGED",
        "max_retries": 3,
        "retry_count": 1,
        "brand_new_field_hunar_added": "whatever",
    }
    client = make_client(lambda request: httpx.Response(200, json=body))

    call = await client.get_call("call-1")

    assert call.status == "VOICEMAIL_DROPPED"
    assert call.lifecycle_status == "SOMETHING_NEW"
    assert call.engagement_status == "PARTIALLY_ENGAGED"
    # The response-side name, not the request-side max_retry_count.
    assert call.max_retries == 3
    await client.aclose()
