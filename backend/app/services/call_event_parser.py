"""Turn a raw webhook body into a `CallUpdate`.

Verified against real captured deliveries. See `fixtures/observed_shapes.md` for
the full record; the short version is below, because the shape of the input is
the entire reason this file exists.

**All four event types were captured**, across two real calls — one unanswered,
one answered by a human and engaged:

| Event | Carries |
| --- | --- |
| `call_status_updated` | the full status block: `status`, `lifecycle_status`, `answered_by`, `retry_count`, `retries_left`, `started_at`, `ended_at`, durations |
| `call_summary` | the same block, plus `result` and `recording_url` |
| `call_recording_done` | **five fields only**: `agent_id`, `call_id`, `event_type`, `request_id`, `recording_url` |
| `call_result_done` | **five fields only**: `agent_id`, `call_id`, `event_type`, `request_id`, `result` |

Two consequences that shape the code below.

**The recording and result events say nothing about where the call is.** No
`status`, no `lifecycle_status`, no `retry_count`. The state machine has to cope
with an update that only carries one fact, which is why `should_apply` reads a
missing `retry_count` as the value already on the record rather than as attempt
zero, and why `result` and `recording_url` are applied ahead of the staleness gate.

**Four fields never appear in any webhook**, confirmed on a connected and engaged
call so it is not an artefact of the unanswered one: `engagement_status`,
`call_ended_by`, `redial_status` and `user_speech_duration`. The call detail API
returns all four. Reading them here would be reading keys that are always absent,
so this parser does not try — they reach the record only through reconciliation
polling the API. `CallUpdate` still declares them for exactly that path.

Defensive throughout: this is fed bytes from the public internet that have passed
an HMAC check and nothing else. It returns None rather than raising, and the
caller records that as an errored event instead of letting one unreadable body
wedge the pipeline.
"""

import json
import logging
from datetime import datetime
from typing import Any

from app.services.call_state import CallUpdate

logger = logging.getLogger(__name__)

# Webhook bodies are flat objects with the id at the top level. There is no
# envelope: an earlier version of this file tried `data`, `call`, `payload` and
# `result` as possible wrappers, which was guesswork, and `result` is a real key
# on a `call_result_done` body — so that fallback could have picked the result
# object up as the call object and parsed nonsense out of it.
#
# `call_id` is the only id key. The call detail API uses `id` instead, which is
# why a webhook body must never be parsed with the `Call` model: that model
# requires `id` and correctly rejects every webhook we captured.
_ID_KEY = "call_id"


def parse_event(event_type: str, raw_body: str) -> CallUpdate | None:
    """None means "could not interpret", never an exception."""
    try:
        payload = json.loads(raw_body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    call_id = _string(payload, _ID_KEY)
    if not call_id:
        return None

    try:
        return CallUpdate(
            hunar_call_id=call_id,
            # Absent on the recording and result events; None there is correct.
            status=_string(payload, "status"),
            lifecycle_status=_string(payload, "lifecycle_status"),
            answered_by=_string(payload, "answered_by"),
            # The response-side name is `max_retries`; `retry_count` is the
            # separate "attempts so far" counter and the one the state machine
            # orders on.
            retry_count=_int(payload, "retry_count"),
            retries_left=_int(payload, "retries_left"),
            next_retry_scheduled_at=_datetime(payload, "next_retry_scheduled_at"),
            recording_url=_string(payload, "recording_url"),
            # Observed as `{}` rather than null when the call produced nothing.
            # That is why `apply_call_update` tests truthiness and not `is not
            # None`: the real sequence is call_result_done delivering a result,
            # then a reconcile poll returning `{}`, which an `is not None` test
            # would write straight over the top of it.
            result=_dict(payload, "result"),
            # duration_minutes is deliberately not read. It is duration_seconds
            # divided by 60 — the same fact twice, and two things to keep in
            # agreement for no gain.
            duration_seconds=_float(payload, "duration_seconds"),
            started_at=_datetime(payload, "started_at"),
            ended_at=_datetime(payload, "ended_at"),
        )
    except Exception:  # pragma: no cover - belt and braces around a public input
        logger.exception("unexpected failure parsing %s event", event_type)
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
        # Observed format: "2026-09-05T03:23:34.710184Z" and "...T03:24:34Z".
        # fromisoformat handles the Z suffix from 3.11 onward.
        return datetime.fromisoformat(value)
    except ValueError:
        return None
