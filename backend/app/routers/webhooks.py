"""Inbound Hunar webhooks: receive, verify, persist, acknowledge. Nothing else.

This endpoint deliberately does not interpret what it stores. Hunar times out
around 15 seconds and retries at 1, 2, 4 and 8 minutes before dropping an event
permanently, so the only job here is to get the bytes safely into the database
and return 200. Interpretation happens in `services/call_state.py`.

The two error rules that are easy to get backwards, and expensive when you do:

* A signature failure is 401 and Hunar should not retry it. Anything that is our
  fault, a database hiccup or a bug, must surface as a 5xx so Hunar *does*
  retry. Returning 200 on our own failure silently loses the event.
* An unknown `hunar_call_id` is not an error. See `_persist` below.
"""

import hashlib
import json
import logging
import time
from collections import deque
from enum import StrEnum

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.integrations.hunar.signature import timestamp_within_skew, verify_signature
from app.models import CallEvent
from app.services.call_state import process_call_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class HunarEventType(StrEnum):
    CALL_STATUS_UPDATED = "call_status_updated"
    CALL_RECORDING_DONE = "call_recording_done"
    CALL_RESULT_DONE = "call_result_done"
    CALL_SUMMARY = "call_summary"


_EVENT_TYPES = {event.value for event in HunarEventType}

# One process-wide sliding window. Not per-IP: every request comes from Hunar,
# so a per-IP bucket would be one bucket anyway.
#
# The cap is generous because a finishing campaign is legitimately bursty: 1000
# calls times four events is 4000 events arriving over minutes, and throttling
# real traffic is worse than the flood it prevents. A 429 is safe regardless,
# because Hunar retries it and reconciliation catches whatever it drops.
#
# This resets on every cold start, which on Render's free tier is often. That is
# acceptable: signature verification is the real gate. This only stops an
# unauthenticated flood from exhausting the database connection pool before the
# HMAC check gets a chance to reject it.
_RATE_LIMIT_MAX_REQUESTS = 600
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_recent_requests: deque[float] = deque()


def _rate_limited() -> bool:
    now = time.monotonic()
    while _recent_requests and now - _recent_requests[0] > _RATE_LIMIT_WINDOW_SECONDS:
        _recent_requests.popleft()
    if len(_recent_requests) >= _RATE_LIMIT_MAX_REQUESTS:
        return True
    _recent_requests.append(now)
    return False


def extract_call_id(raw_body: bytes) -> str | None:
    """Shallow, defensive lookup of the Hunar call id. Never raises.

    Returns None for a body that is not JSON, is not an object, or simply does
    not carry the id. None is a valid outcome, not a failure: see `_persist`.

    The key names are a best guess pending the live capture in task 3.5, which
    is why this tries several and gives up quietly rather than assuming one.
    """
    try:
        payload = json.loads(raw_body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("call_id", "id", "hunar_call_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


@router.post("/hunar/{event_type}")
async def receive_hunar_webhook(
    event_type: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    started = time.perf_counter()

    # Checked first so an unknown path costs nothing: no body read, no database.
    if event_type not in _EVENT_TYPES:
        raise HTTPException(status_code=404, detail="Unknown event type")

    if _rate_limited():
        raise HTTPException(status_code=429, detail="Too many webhook deliveries")

    # Checked before the body is even read: with no key we cannot verify
    # anything, so nothing that arrives is trustworthy. 503 rather than 401
    # because this is our misconfiguration, not a caller's bad signature, and an
    # empty key would otherwise make verify_signature an HMAC keyed on "" that
    # anyone could forge.
    signing_key, key_env_var = settings.webhook_signing_key()
    if not signing_key:
        logger.warning(
            "webhook %s refused: %s is not set, cannot verify signatures",
            event_type, key_env_var,
        )
        raise HTTPException(
            status_code=503, detail="Webhook verification is not configured"
        )

    # Raw bytes, before anything parses them. The HMAC is computed over exactly
    # what Hunar put on the wire, so reading through a parser and re-serialising
    # would change key order and whitespace and invalidate a valid signature.
    raw_body = await request.body()

    timestamp = request.headers.get("X-Hunar-Timestamp")
    signature = request.headers.get("X-Hunar-Signature")
    if not timestamp or not signature:
        _log(event_type, None, False, started, "missing signature headers")
        raise HTTPException(status_code=401, detail="Missing signature headers")

    if not timestamp_within_skew(timestamp, settings.hunar_webhook_skew_seconds):
        _log(event_type, None, False, started, "timestamp outside skew window")
        raise HTTPException(status_code=401, detail="Stale or invalid timestamp")

    if not verify_signature(signing_key, timestamp, raw_body, signature):
        _log(event_type, None, False, started, "signature mismatch")
        raise HTTPException(status_code=401, detail="Invalid signature")

    hunar_call_id = extract_call_id(raw_body)
    duplicate = await _persist(session, event_type, raw_body, hunar_call_id, background_tasks)

    _log(event_type, hunar_call_id, True, started, "duplicate" if duplicate else "stored")
    return {"status": "duplicate" if duplicate else "accepted"}


async def _persist(
    session: AsyncSession,
    event_type: str,
    raw_body: bytes,
    hunar_call_id: str | None,
    background_tasks: BackgroundTasks,
) -> bool:
    """Store the event. Returns True if it was already known.

    `hunar_call_id` may be None and that is fine. A webhook can beat our own
    dispatch write, or carry an id under a key we do not recognise yet. The
    event is stored unlinked and task 6 resolves it. Rejecting it with a 4xx
    would buy four Hunar retries and then permanent loss of the event.
    """
    event = CallEvent(
        hunar_call_id=hunar_call_id,
        event_type=event_type,
        raw_body=raw_body.decode("utf-8", errors="replace"),
        # Over the raw bytes, so redelivery of a byte-identical body collides.
        payload_hash=hashlib.sha256(raw_body).hexdigest(),
    )
    session.add(event)
    try:
        await session.commit()
    except IntegrityError:
        # Insert first and catch the collision, rather than SELECT-then-INSERT:
        # Hunar's retries can overlap in flight and check-then-insert races
        # itself. The unique index on payload_hash is the only reliable arbiter.
        await session.rollback()
        logger.debug("duplicate %s webhook ignored", event_type)
        return True

    # In-process rather than a queue: Render's free tier has no worker process,
    # so Celery or RQ would need a second service we cannot run. The cost is
    # that work in flight is lost if the instance restarts, which is exactly
    # what the reconciliation cron in task 7 exists to repair.
    background_tasks.add_task(process_call_event, event.id)
    return False


def _log(
    event_type: str,
    hunar_call_id: str | None,
    signature_ok: bool,
    started: float,
    outcome: str,
) -> None:
    # Never the raw body, never custom_data, never a phone number, never the key.
    logger.info(
        "webhook %s call=%s signature=%s %s in %.0f ms",
        event_type,
        hunar_call_id or "-",
        "ok" if signature_ok else "FAILED",
        outcome,
        (time.perf_counter() - started) * 1000,
    )


def reset_rate_limit() -> None:
    """Test helper: the window is module state and would leak between tests."""
    _recent_requests.clear()


__all__ = ["HunarEventType", "extract_call_id", "reset_rate_limit", "router"]
