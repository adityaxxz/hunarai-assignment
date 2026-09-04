"""Turn a raw webhook body into a `CallUpdate`.

=============================================================================
UNVERIFIED AGAINST LIVE PAYLOADS. Corrected in task 6b.

Every field name and every assumption about nesting below is read off Hunar's
published call-detail example, not off a webhook we have actually received. The
capture in task 3.5 has not run yet.

This is the ONLY file that should need to change when the fixtures land. If
task 6b ends up editing anything else, the seam was drawn in the wrong place.
=============================================================================

Defensive throughout: this is fed bytes from the public internet that have
passed an HMAC check and nothing else. It returns None rather than raising, and
the caller records that as an errored event instead of letting one unreadable
body wedge the pipeline.
"""

import json
import logging
from datetime import datetime
from typing import Any

from app.services.call_state import CallUpdate

logger = logging.getLogger(__name__)

# The webhook may deliver the call object at the top level or wrapped. Tried in
# order; the first one that is an object with an id wins.
_ENVELOPE_KEYS = ("data", "call", "payload", "result")

# Best guess, in preference order, at where the call id lives.
_ID_KEYS = ("call_id", "id", "hunar_call_id")


def parse_event(event_type: str, raw_body: str) -> CallUpdate | None:
    """None means "could not interpret", never an exception."""
    try:
        payload = json.loads(raw_body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    call = _locate_call_object(payload)
    if call is None:
        return None

    call_id = _first_string(call, _ID_KEYS) or _first_string(payload, _ID_KEYS)
    if not call_id:
        return None

    try:
        return CallUpdate(
            hunar_call_id=call_id,
            status=_string(call, "status"),
            lifecycle_status=_string(call, "lifecycle_status"),
            engagement_status=_string(call, "engagement_status"),
            answered_by=_string(call, "answered_by"),
            call_ended_by=_string(call, "call_ended_by"),
            redial_status=_string(call, "redial_status"),
            # Response-side name. The request sends retry_config.max_retry_count
            # and the response returns max_retries; retry_count is the separate
            # "attempts so far" counter and is the one the state machine orders on.
            retry_count=_int(call, "retry_count"),
            retries_left=_int(call, "retries_left"),
            next_retry_scheduled_at=_datetime(call, "next_retry_scheduled_at"),
            recording_url=_string(call, "recording_url"),
            result=_dict(call, "result"),
            duration_seconds=_float(call, "duration_seconds"),
            user_speech_duration=_float(call, "user_speech_duration"),
            started_at=_datetime(call, "started_at"),
            ended_at=_datetime(call, "ended_at"),
        )
    except Exception:  # pragma: no cover - belt and braces around a public input
        logger.exception("unexpected failure parsing %s event", event_type)
        return None


def _locate_call_object(payload: dict[str, Any]) -> dict[str, Any] | None:
    if any(isinstance(payload.get(key), str) and payload[key] for key in _ID_KEYS):
        return payload
    for key in _ENVELOPE_KEYS:
        nested = payload.get(key)
        if isinstance(nested, dict) and any(
            isinstance(nested.get(id_key), str) and nested[id_key] for id_key in _ID_KEYS
        ):
            return nested
    return None


def _first_string(source: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _string(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    return value if isinstance(value, str) and value else None


def _int(source: dict[str, Any], key: str) -> int | None:
    value = source.get(key)
    # bool is an int subclass and would silently become 0 or 1.
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _float(source: dict[str, Any], key: str) -> float | None:
    value = source.get(key)
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _dict(source: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = source.get(key)
    return value if isinstance(value, dict) else None


def _datetime(source: dict[str, Any], key: str) -> datetime | None:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        # Hunar sends "2025-09-23T12:20:16.503Z"; fromisoformat handles the Z
        # suffix from 3.11 onward.
        return datetime.fromisoformat(value)
    except ValueError:
        return None
