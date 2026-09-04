"""Webhook receiver: verification and idempotent persistence.

Signatures are built with the same helper the app verifies with, keyed on the
dummy value conftest puts in the environment. No real key appears here.
"""

import json
import time

from sqlalchemy import func, select

from app.config import settings
from app.integrations.hunar.signature import compute_signature
from app.models import CallEvent
from tests.conftest import SIGNING_KEY

URL = "/webhooks/hunar/call_status_updated"

BODY = json.dumps(
    {"call_id": "call-abc-123", "status": "RINGING", "lifecycle_status": "IN_PROGRESS"}
).encode()


def headers(body: bytes, *, timestamp: str | None = None, signature: str | None = None) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    return {
        "X-Hunar-Timestamp": timestamp,
        "X-Hunar-Signature": signature or compute_signature(SIGNING_KEY, timestamp, body),
        "Content-Type": "application/json",
    }


async def count_events(session) -> int:
    return (await session.execute(select(func.count()).select_from(CallEvent))).scalar_one()


async def test_valid_signature_is_persisted(client, session) -> None:
    response = await client.post(URL, content=BODY, headers=headers(BODY))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}

    event = (await session.execute(select(CallEvent))).scalar_one()
    assert event.event_type == "call_status_updated"
    assert event.hunar_call_id == "call-abc-123"
    # Stored verbatim, which is what makes the signature re-checkable later.
    assert event.raw_body == BODY.decode()
    assert event.processed_at is None


async def test_tampered_body_is_rejected_and_stores_nothing(client, session) -> None:
    """Signature computed over the original body, a different body sent."""
    tampered = json.dumps({"call_id": "call-abc-123", "status": "COMPLETED"}).encode()

    response = await client.post(URL, content=tampered, headers=headers(BODY))

    assert response.status_code == 401
    assert await count_events(session) == 0


async def test_stale_timestamp_is_rejected(client, session) -> None:
    old = str(int(time.time()) - 400)

    response = await client.post(URL, content=BODY, headers=headers(BODY, timestamp=old))

    assert response.status_code == 401
    assert await count_events(session) == 0


async def test_second_signature_segment_is_accepted(client) -> None:
    """Hunar signs with both keys during a rotation, so any segment may match."""
    valid = compute_signature(SIGNING_KEY, (ts := str(int(time.time()))), BODY)
    rotating = f"{compute_signature('the-old-key-being-retired', ts, BODY)},{valid}"

    response = await client.post(
        URL, content=BODY, headers=headers(BODY, timestamp=ts, signature=rotating)
    )

    assert response.status_code == 200


async def test_redelivery_is_idempotent(client, session) -> None:
    """Hunar can deliver the same event twice. Two 200s, one row."""
    sent = headers(BODY)

    first = await client.post(URL, content=BODY, headers=sent)
    second = await client.post(URL, content=BODY, headers=sent)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "duplicate"}
    assert await count_events(session) == 1


async def test_unknown_event_type_is_404(client, session) -> None:
    response = await client.post(
        "/webhooks/hunar/call_something_invented", content=BODY, headers=headers(BODY)
    )

    assert response.status_code == 404
    assert await count_events(session) == 0


async def test_missing_signing_key_returns_503(client, session, monkeypatch) -> None:
    """No key means we cannot verify anything, so nothing arriving is trustworthy.

    503 and not 401: this is our misconfiguration, not a caller's bad signature.
    Without this guard the deployed demo would verify against an HMAC keyed on
    an empty string, which anyone could forge.
    """
    monkeypatch.setattr(settings, "hunar_api_key", "")

    response = await client.post(URL, content=BODY, headers=headers(BODY))

    assert response.status_code == 503
    assert await count_events(session) == 0
