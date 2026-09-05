"""Parser tests built from real captured webhooks.

The payloads below are copied verbatim from the task 3.5 capture, with the two
phone numbers redacted. They are inlined rather than read from `fixtures/raw/`
because that directory is gitignored — it holds a real number and real
signatures — so a test reading from it would pass here and fail on a fresh clone.

If Hunar changes a payload shape, these are what break.
"""

import json

from app.services.call_event_parser import parse_event

CALL_ID = "92b0b3ea-ea2a-4eed-b2fb-8a072236e13d"

# Real /hunar/status body from the connected call.
STATUS_EVENT = {
    "agent_id": "09c551dd-5a3a-4a10-b26f-e6aa2b5833f1",
    "answered_by": "HUMAN",
    "call_id": CALL_ID,
    "created_at": "2026-09-05T03:23:10.745000Z",
    "duration_minutes": 0.5,
    "duration_seconds": 30.0,
    "ended_at": "2026-09-05T03:24:34Z",
    "event_type": "call_status_updated",
    "from_phone_number": "+910000000000",
    "lifecycle_status": "COMPLETED",
    "max_retries": 0,
    "next_retry_scheduled_at": None,
    "request_id": "ARFDE-CAPTURE-20260905-032303",
    "retries_left": 0,
    "retry_count": 0,
    "retry_reason": None,
    "started_at": "2026-09-05T03:23:34.710184Z",
    "status": "COMPLETED",
    "timezone": "Asia/Kolkata",
    "to_number": "+910000000000",
}

# Real /hunar/recording body. Five fields, no status of any kind.
RECORDING_EVENT = {
    "agent_id": "09c551dd-5a3a-4a10-b26f-e6aa2b5833f1",
    "call_id": CALL_ID,
    "event_type": "call_recording_done",
    "recording_url": "https://example-bucket.s3.ap-south-1.amazonaws.com/call/recording/x/y_0_plivo.wav",
    "request_id": "ARFDE-CAPTURE-20260905-032303",
}

# Real /hunar/result body. The maker-checker output, structured, no free text.
RESULT_EVENT = {
    "agent_id": "09c551dd-5a3a-4a10-b26f-e6aa2b5833f1",
    "call_id": CALL_ID,
    "event_type": "call_result_done",
    "request_id": "ARFDE-CAPTURE-20260905-032303",
    "result": {"availability": "a week", "identity_confirmed": True, "open_to_work": True},
}


def parse(payload: dict) -> object:
    return parse_event(payload["event_type"], json.dumps(payload))


def test_status_event_uses_call_id_not_id() -> None:
    """Webhook bodies carry `call_id`; only the call detail API uses `id`."""
    update = parse(STATUS_EVENT)

    assert update is not None
    assert update.hunar_call_id == CALL_ID
    assert update.status == "COMPLETED"
    assert update.lifecycle_status == "COMPLETED"
    assert update.answered_by == "HUMAN"
    assert update.retry_count == 0
    assert update.duration_seconds == 30.0
    assert update.started_at is not None and update.ended_at is not None


def test_status_event_has_no_engagement_status() -> None:
    """Confirmed against a connected, ENGAGED call: the API reports ENGAGED, the
    webhook does not carry the key at all. Engagement is an API-only signal, so
    any funnel stage using it has to come from reconciliation."""
    update = parse(STATUS_EVENT)

    assert update is not None
    assert update.engagement_status is None
    assert update.user_speech_duration is None


def test_recording_event_carries_only_the_url() -> None:
    """Five fields, no status and no retry_count. The state machine has to cope
    with an update that says nothing about where the call is."""
    update = parse(RECORDING_EVENT)

    assert update is not None
    assert update.hunar_call_id == CALL_ID
    assert update.recording_url.endswith("_0_plivo.wav")
    assert update.status is None
    assert update.lifecycle_status is None
    assert update.retry_count is None


def test_result_event_carries_the_structured_result() -> None:
    """result_schema types are honoured: booleans come back as JSON booleans, not
    as the strings "true"/"false", so nothing here needs parsing."""
    update = parse(RESULT_EVENT)

    assert update is not None
    assert update.result == {
        "availability": "a week",
        "identity_confirmed": True,
        "open_to_work": True,
    }
    assert update.result["open_to_work"] is True
    assert update.status is None


# Real /hunar/summary body from the connected call, delivered 372s after the call
# ended. The only event that carries status, result and recording together.
SUMMARY_EVENT = {
    **STATUS_EVENT,
    "event_type": "call_summary",
    "recording_url": RECORDING_EVENT["recording_url"],
    "result": RESULT_EVENT["result"],
}


def test_summary_event_carries_status_result_and_recording_together() -> None:
    update = parse(SUMMARY_EVENT)

    assert update is not None
    assert update.status == "COMPLETED"
    assert update.result == RESULT_EVENT["result"]
    assert update.recording_url == RECORDING_EVENT["recording_url"]
    # Still not carried, even on the combined terminal event.
    assert update.engagement_status is None
