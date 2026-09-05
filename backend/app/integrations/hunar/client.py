"""Typed async client for the Hunar Voice API.

Everything outbound to Hunar goes through here. Method signatures are kept
narrow and boring on purpose: the simulator in task 4 has to implement the same
shapes, and anything clever here becomes something clever to reimplement there.
"""

import asyncio
import logging
import os
import sys
import time
from typing import Any

import httpx

from app.integrations.hunar.errors import (
    HunarAuthError,
    HunarBadRequestError,
    HunarError,
    HunarNotFoundError,
    HunarQuotaError,
    HunarServerError,
    HunarTimeoutError,
    HunarValidationError,
)
from app.integrations.hunar.types import (
    Agent,
    AgentCreate,
    AgentUpdate,
    BulkCallCreate,
    Call,
    CallCreate,
    Page,
    PhoneNumber,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.voice.hunar.ai/external/v1"

# Escape hatch for a deliberate live call from inside a test. Nothing in this
# repo sets it. scripts/smoke_hunar.py does not need it either, because it runs
# outside pytest.
LIVE_CLIENT_OPT_IN = "HUNAR_ALLOW_LIVE_CLIENT"


class LiveClientBlockedError(RuntimeError):
    """Raised when a real client is constructed during a test run."""


def _under_pytest() -> bool:
    # PYTEST_CURRENT_TEST is set per test; sys.modules catches collection and
    # module import time, before the first test has started.
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules

_RETRYABLE_STATUS = {500, 502, 503, 504}

_ERROR_BY_STATUS: dict[int, type[HunarError]] = {
    400: HunarBadRequestError,
    401: HunarAuthError,
    402: HunarQuotaError,
    404: HunarNotFoundError,
    422: HunarValidationError,
}


class HunarClient:
    # Takes its credentials rather than reading settings, so this module has no
    # config dependency and the composition points (the provider switch in task 4,
    # the smoke script) are the only places that know where the key comes from.
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # The guard lives at construction, not behind a fixture, because a
        # fixture is something a future test can forget to apply — and this
        # already happened: a router calling get_voice_provider() under
        # DEMO_MODE=false put real requests on api.voice.hunar.ai and only
        # surfaced because the key 401'd. Had it resolved, create_agent would
        # have written to a shared production org that already holds 100+ other
        # people's agents, and create_call would have spent real calling minutes.
        #
        # An injected transport is exempt: that is how the client's own tests run
        # against httpx.MockTransport, and supplying a real one is a deliberate
        # act rather than an accident.
        if transport is None and _under_pytest() and not os.environ.get(LIVE_CLIENT_OPT_IN):
            raise LiveClientBlockedError(
                "HunarClient was constructed during a pytest run, which would put "
                "real requests on api.voice.hunar.ai: create_agent writes to a "
                "shared production org and create_call spends real calling "
                "minutes. Use HunarSimulator, or inject an httpx transport. If a "
                f"live call from a test is genuinely intended, set {LIVE_CLIENT_OPT_IN}=1."
            )

        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        # The key goes into the header and is never kept as an attribute, so it
        # cannot surface through a repr of this object.
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"X-API-Key": api_key},
            # Split deliberately: a dead host should fail fast, but a bulk
            # dispatch of a few thousand recipients legitimately takes a while.
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "HunarClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def list_agents(self, *, page: int = 1, page_size: int | None = None) -> Page[Agent]:
        payload = await self._request(
            "GET", "agents/", params={"page": page, "page_size": page_size}
        )
        return Page[Agent].model_validate(payload)

    async def get_agent(self, agent_id: str) -> Agent:
        return Agent.model_validate(await self._request("GET", f"agents/{agent_id}/"))

    async def create_agent(self, payload: AgentCreate) -> Agent:
        body = await self._request("POST", "agents/", json=_dump(payload))
        return Agent.model_validate(body)

    async def update_agent(self, agent_id: str, payload: AgentUpdate) -> Agent:
        body = await self._request("PUT", f"agents/{agent_id}/", json=_dump(payload))
        return Agent.model_validate(body)

    async def create_call(self, payload: CallCreate) -> Call:
        return Call.model_validate(await self._request("POST", "calls/", json=_dump(payload)))

    async def create_bulk_calls(self, payload: BulkCallCreate) -> list[Call]:
        # Unlike every other list-shaped response, bulk create returns a bare
        # JSON array rather than the paginated envelope.
        body = await self._request("POST", "calls/bulk/", json=_dump(payload))
        return [Call.model_validate(item) for item in body]

    async def list_calls(
        self,
        *,
        campaign_id: str | None = None,
        agent_id: list[str] | None = None,
        status: list[str] | None = None,
        page: int = 1,
        page_size: int | None = None,
    ) -> Page[Call]:
        """Only the filters Hunar documents. There is deliberately no request_id,
        lifecycle_status or date-range argument: those are not documented query
        params, and an unrecognised filter is silently ignored by the API, which
        would make reconciliation quietly page over the wrong set of calls."""
        payload = await self._request(
            "GET",
            "calls/",
            params={
                "campaign_id": campaign_id,
                "agent_id": agent_id,
                "status": status,
                "page": page,
                "page_size": page_size,
            },
        )
        return Page[Call].model_validate(payload)

    async def get_call(self, call_id: str) -> Call:
        return Call.model_validate(await self._request("GET", f"calls/{call_id}/"))

    async def list_numbers(self, *, page: int = 1, page_size: int | None = None) -> Page[PhoneNumber]:
        payload = await self._request(
            "GET", "numbers/", params={"page": page, "page_size": page_size}
        )
        return Page[PhoneNumber].model_validate(payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request, retrying only what is safe to retry.

        A plain loop rather than tenacity: this is the whole retry policy, it is
        eleven lines, and a dependency to express it would be a dependency to
        justify in the interview.
        """
        query = {k: v for k, v in (params or {}).items() if v is not None}

        for attempt in range(1, self._max_attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._client.request(method, path, json=json, params=query)
            except httpx.TransportError as exc:
                # Covers connect errors, read timeouts and pool timeouts alike.
                if attempt == self._max_attempts:
                    logger.warning(
                        "hunar %s %s transport failure after %d attempts: %s",
                        method, path, attempt, type(exc).__name__,
                    )
                    raise HunarTimeoutError(
                        f"Could not reach Hunar after {attempt} attempts: {type(exc).__name__}"
                    ) from exc
                await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            elapsed_ms = (time.perf_counter() - started) * 1000
            # Method, path, status and duration only. Never the body: it carries
            # phone numbers and custom_data, and never the headers: they carry the key.
            logger.info(
                "hunar %s %s -> %d in %.0f ms", method, path, response.status_code, elapsed_ms
            )

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_attempts:
                await asyncio.sleep(self._backoff_seconds * attempt)
                continue

            if response.status_code >= 400:
                raise _error_for(response)

            return _json_or_none(response)

        raise AssertionError("unreachable: loop either returns or raises")


def _dump(payload: Any) -> dict[str, Any]:
    # exclude_none so an omitted retry_config stays omitted rather than being
    # sent as null, which Hunar rejects differently from absent.
    return payload.model_dump(mode="json", exclude_none=True)


def _json_or_none(response: httpx.Response) -> Any:
    if not response.content:
        return None
    return response.json()


def _error_for(response: httpx.Response) -> HunarError:
    message, details = _parse_error_body(response)
    error_cls = _ERROR_BY_STATUS.get(response.status_code)
    if error_cls is None:
        error_cls = HunarServerError if response.status_code >= 500 else HunarError
    return error_cls(message, status_code=response.status_code, details=details)


def _parse_error_body(response: httpx.Response) -> tuple[str, list[dict[str, Any]]]:
    try:
        body = response.json()
    except ValueError:
        # A proxy or gateway error page, not Hunar. Truncated because it can be
        # an entire HTML document.
        return (response.text or response.reason_phrase or "Unknown error")[:300], []

    if isinstance(body, dict):
        message = body.get("message") or body.get("detail") or "Unknown error"
        details = body.get("details")
        return str(message), details if isinstance(details, list) else []
    return str(body)[:300], []
