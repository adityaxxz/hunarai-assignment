"""Parser tests, one per webhook event type.

The payloads are synthetic but structurally exact: same keys, same types, same
nulls as the deliveries recorded in `fixtures/observed_shapes.md`. Nothing real
appears here — no call ids, no phone numbers, no S3 URLs — because
`fixtures/raw/` is gitignored precisely for holding those, and copying them into
a committed test would defeat that.

Written out per event rather than derived from one another, so that when Hunar
changes one event's shape the diff points at that event.
"""

import json

from app.services.call_event_parser import parse_event

CALL_ID = "00000000-1111-2222-3333-444444444444"
AGENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
REQUEST_ID = "campaign-demo-001"
RECORDING_URL = "https://recordings.invalid/call/synthetic_0.wav"
RESULT = {"open_to_work": True, "availability": "within a week"}

# --- the four observed shapes ---------------------------------------------

STATUS_EVENT = {
    "agent_id": AGENT_ID,
    "answered_by": "HUMAN",
    "call_id": CALL_ID,
    "created_at": "2026-01-01T10:00:00.000000Z",
    "duration_minutes": 0.5,
    "duration_seconds": 30.0,
    "ended_at": "2026-01-01T10:01:30Z",
    "event_type": "call_status_updated",
    "from_phone_number": "+910000000000",
    "lifecycle_status": "COMPLETED",
    "max_retries": 0,
    "next_retry_scheduled_at": None,
    "request_id": REQUEST_ID,
    "retries_left": 0,
    "retry_count": 0,
    "retry_reason": None,
    "started_at": "2026-01-01T10:01:00.000000Z",
    "status": "COMPLETED",
    "timezone": "Asia/Kolkata",
    "to_number": "+910000000000",
}

SUMMARY_EVENT = {
    **STATUS_EVENT,
    "event_type": "call_summary",
    "recording_url": RECORDING_URL,
    "result": RESULT,
}

RECORDING_EVENT = {
    "agent_id": AGENT_ID,
    "call_id": CALL_ID,
    "event_type": "call_recording_done",
    "recording_url": RECORDING_URL,
    "request_id": REQUEST_ID,
}

RESULT_EVENT = {
    "agent_id": AGENT_ID,
    "call_id": CALL_ID,
    "event_type": "call_result_done",
    "request_id": REQUEST_ID,
    "result": RESULT,
}


def parse(payload: dict):
    return parse_event(payload["event_type"], json.dumps(payload))


# --- one per event type ----------------------------------------------------


def test_call_status_updated() -> None:
    update = parse(STATUS_EVENT)

    assert update is not None
    # `call_id`, not `id`. The call detail API is the one that uses `id`, which
    # is why the Call model cannot be used to parse a webhook body.
    assert update.hunar_call_id == CALL_ID
    assert update.status == "COMPLETED"
    assert update.lifecycle_status == "COMPLETED"
    assert update.answered_by == "HUMAN"
    assert update.retry_count == 0
    assert update.retries_left == 0
    assert update.duration_seconds == 30.0
    assert update.started_at is not None
    assert update.ended_at is not None
    # Carried by the API, never by a webhook. Reconciliation is the only route.
    assert update.engagement_status is None
    assert update.call_ended_by is None
    assert update.redial_status is None
    assert update.user_speech_duration is None


def test_call_summary_carries_status_result_and_recording_together() -> None:
    update = parse(SUMMARY_EVENT)

    assert update is not None
    assert update.status == "COMPLETED"
    assert update.result == RESULT
    assert update.recording_url == RECORDING_URL
    # Still absent even on the combined terminal event.
    assert update.engagement_status is None
    assert update.user_speech_duration is None


def test_call_recording_done_says_nothing_about_call_state() -> None:
    """Five fields. The state machine has to accept an update that carries one
    fact and no idea which attempt it belongs to."""
    update = parse(RECORDING_EVENT)

    assert update is not None
    assert update.hunar_call_id == CALL_ID
    assert update.recording_url == RECORDING_URL
    assert update.status is None
    assert update.lifecycle_status is None
    assert update.retry_count is None
    assert update.result is None


def test_call_result_done_carries_the_structured_result() -> None:
    """result_schema types are honoured, so booleans arrive as JSON booleans and
    there is no free text to parse anywhere."""
    update = parse(RESULT_EVENT)

    assert update is not None
    assert update.result == RESULT
    assert update.result["open_to_work"] is True
    assert update.status is None
    assert update.retry_count is None


# --- the shape details that cost us a bug ----------------------------------


def test_empty_result_is_parsed_as_an_empty_dict_not_none() -> None:
    """Hunar sends `{}`, not null, when a call produced no result.

    This is what makes the truthiness test in `apply_call_update` load-bearing:
    the observed sequence is `call_result_done` delivering a real result, then a
    reconcile poll returning `{}`. An `is not None` test would erase it.
    """
    update = parse({**SUMMARY_EVENT, "result": {}})

    assert update is not None
    assert update.result == {}
    assert not update.result


def test_duration_minutes_is_deliberately_ignored() -> None:
    """It is duration_seconds / 60. Storing both is two sources of one fact."""
    update = parse(STATUS_EVENT)

    assert update is not None
    assert update.duration_seconds == 30.0
    assert not hasattr(update, "duration_minutes")


def test_a_result_object_is_never_mistaken_for_the_call_object() -> None:
    """The removed envelope fallback tried `data`, `call`, `payload` and `result`
    as possible wrappers. `result` is a real key on a `call_result_done` body, so
    a result that happened to look like a call could have been picked up as one.
    Bodies are flat; there is no envelope handling left to go wrong."""
    trap = {
        "call_id": CALL_ID,
        "event_type": "call_result_done",
        "result": {"call_id": "not-the-call-id", "status": "FAILED"},
    }

    update = parse(trap)

    assert update is not None
    assert update.hunar_call_id == CALL_ID
    assert update.status is None
    assert update.result == {"call_id": "not-the-call-id", "status": "FAILED"}


def test_malformed_input_returns_none_and_never_raises() -> None:
    for raw in (
        "<html>502 Bad Gateway</html>",
        "",
        "[1, 2, 3]",
        '{"status": "COMPLETED"}',  # no call_id
        '{"call_id": null}',
        '{"call_id": 12345}',  # ids are strings
    ):
        assert parse_event("call_status_updated", raw) is None
