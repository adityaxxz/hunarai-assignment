"""Payload shapes and per-call state for the simulator.

Split out of `simulator.py` so that file is about how a call *progresses* and
this one is about what the payloads *look like*. Both shapes are copied from
`fixtures/observed_shapes.md`.

The single most important thing here: **the webhook body is not the call-detail
body.** `call_id` not `id`, `to_number` not `mobile_number`, and no
engagement_status, call_ended_by, redial_status or user_speech_duration on any
event — confirmed against a connected, engaged call, so that is not an artefact
of the unanswered one.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Synthetic. Nothing here comes from fixtures/raw/, which holds a real number,
# real call ids and real signatures and is gitignored for that reason.
DEMO_FROM_NUMBER = "+915555500000"
DEMO_RECORDING_HOST = "https://demo-recordings.invalid/call/recording"

# Light hints so a generated result reads like a screening answer rather than
# lorem ipsum. Matched on substrings, never on the exact keys from our capture
# agent, because every requisition declares its own result_schema.
_STRING_HINTS: list[tuple[tuple[str, ...], list[str]]] = [
    (("avail", "notice", "start", "join"), ["immediately", "within a week", "two weeks", "one month"]),
    (("experience", "years", "tenure"), ["6 months", "2 years", "4 years", "7 years"]),
    (("location", "city", "area"), ["Bengaluru", "Whitefield", "Koramangala", "Hebbal"]),
    (("shift", "time"), ["morning shift", "evening shift", "any shift", "weekends only"]),
    (("language", "speak"), ["Kannada and Hindi", "Hindi", "Kannada, Hindi and English"]),
    (("reason", "objection", "concern"), ["pay is low", "too far", "already employed", "none"]),
]

_GENERIC_STRINGS = ["yes", "no", "maybe", "not sure", "will confirm"]


def generate_result(
    result_schema: dict[str, Any], rng: random.Random, qualified: bool
) -> dict[str, Any]:
    """Values matching the *declared* types in the agent's result_schema.

    Observed: Hunar honours the declared type, so `"boolean"` came back as a real
    JSON boolean and `"string"` as a string. Nothing downstream parses free text.
    """
    result: dict[str, Any] = {}
    for key, declared in result_schema.items():
        declared_type = declared if isinstance(declared, str) else "string"
        lowered = key.lower()
        if declared_type.startswith("bool"):
            # Mostly follow the call's overall verdict so the rubric in task 8
            # sees coherent records, but not unanimously.
            result[key] = qualified if rng.random() < 0.8 else not qualified
        elif declared_type.startswith(("int", "number", "float")):
            result[key] = rng.randint(0, 10)
        else:
            pool = _GENERIC_STRINGS
            for needles, options in _STRING_HINTS:
                if any(needle in lowered for needle in needles):
                    pool = options
                    break
            result[key] = rng.choice(pool)
    return result


@dataclass
class SimCall:
    id: str
    agent_id: str
    callee_name: str
    mobile_number: str
    request_id: str | None
    timezone: str
    max_retries: int
    custom_data: dict[str, Any]
    status: str = "NOT_STARTED"
    lifecycle_status: str = "NOT_STARTED"
    engagement_status: str | None = None
    answered_by: str | None = None
    call_ended_by: str | None = None
    redial_status: str | None = None
    retry_count: int = 0
    retries_left: int = 0
    next_retry_scheduled_at: datetime | None = None
    duration_seconds: float = 0.0
    user_speech_duration: float | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result: dict[str, Any] = field(default_factory=dict)
    recording_url: str | None = None
    # False until the consistency delay elapses. Gates what get_call returns,
    # mirroring the API returning result {} at the moment of terminal.
    result_visible: bool = False


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def webhook_body(sim: SimCall, event_type: str) -> dict[str, Any]:
    """The webhook shape. `call_id`, `to_number`, and none of the API-only fields."""
    if event_type == "call_recording_done":
        return {
            "agent_id": sim.agent_id,
            "call_id": sim.id,
            "event_type": event_type,
            "recording_url": sim.recording_url,
            "request_id": sim.request_id,
        }
    if event_type == "call_result_done":
        return {
            "agent_id": sim.agent_id,
            "call_id": sim.id,
            "event_type": event_type,
            "request_id": sim.request_id,
            "result": sim.result,
        }

    body: dict[str, Any] = {
        "agent_id": sim.agent_id,
        "answered_by": sim.answered_by,
        "call_id": sim.id,
        "created_at": _iso(sim.created_at),
        "duration_minutes": round(sim.duration_seconds / 60, 2),
        "duration_seconds": sim.duration_seconds,
        "ended_at": _iso(sim.ended_at),
        "event_type": event_type,
        "from_phone_number": DEMO_FROM_NUMBER,
        "lifecycle_status": sim.lifecycle_status,
        "max_retries": sim.max_retries,
        "next_retry_scheduled_at": _iso(sim.next_retry_scheduled_at),
        "request_id": sim.request_id,
        "retries_left": sim.retries_left,
        "retry_count": sim.retry_count,
        "retry_reason": None,
        "started_at": _iso(sim.started_at),
        "status": sim.status,
        "timezone": sim.timezone,
        "to_number": sim.mobile_number,
    }
    if event_type == "call_summary":
        # Only the summary carries these, and `result` is {} not null when the
        # call produced nothing.
        body["recording_url"] = sim.recording_url
        body["result"] = sim.result if sim.result_visible else {}
    return body


def api_body(sim: SimCall) -> dict[str, Any]:
    """The GET /calls/{id}/ shape, which carries the signals no webhook does."""
    return {
        "id": sim.id,
        "callee_name": sim.callee_name,
        "mobile_number": sim.mobile_number,
        "from_phone_number": DEMO_FROM_NUMBER,
        "agent_id": sim.agent_id,
        "campaign_id": None,
        "language": "ENGLISH",
        "status": sim.status,
        "lifecycle_status": sim.lifecycle_status,
        # API-only signals: never present on any webhook body, so a funnel stage
        # keyed on engagement has to be fed by reconciliation.
        "engagement_status": sim.engagement_status,
        "answered_by": sim.answered_by,
        "call_ended_by": sim.call_ended_by,
        "redial_status": sim.redial_status,
        "user_speech_duration": sim.user_speech_duration if sim.result_visible else None,
        "max_retries": sim.max_retries,
        "retry_count": sim.retry_count,
        "retries_left": sim.retries_left,
        "next_retry_scheduled_at": _iso(sim.next_retry_scheduled_at),
        # Eventual consistency: empty at the moment of terminal, populated once
        # the maker-checker pass and the recording upload finish.
        "recording_url": sim.recording_url if sim.result_visible else None,
        "result": sim.result if sim.result_visible else {},
        "custom_data": sim.custom_data,
        "system_data": {},
        "duration_minutes": round(sim.duration_seconds / 60, 2),
        "duration_seconds": sim.duration_seconds,
        "request_id": sim.request_id,
        "timezone": sim.timezone,
        "created_at": _iso(sim.created_at),
        "updated_at": _iso(datetime.now(timezone.utc)),
        "started_at": _iso(sim.started_at),
        "ended_at": _iso(sim.ended_at),
        "triggered_by": "demo@simulated.invalid",
    }
