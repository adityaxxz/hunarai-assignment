"""Call state machine. No network, no fixtures, no payload shapes except where a
test is explicitly about parsing."""

import json

from sqlalchemy import select

from app.models import (
    AgentVersion,
    Call,
    CallEvent,
    Campaign,
    CampaignKind,
    Candidate,
    CandidateSource,
)
from app.services.call_state import (
    CallUpdate,
    apply_call_update,
    process_call_event,
    resolve_orphan_events,
)

HUNAR_CALL_ID = "hunar-call-1"


async def make_call(session, **overrides) -> Call:
    """Minimum graph a Call needs: campaign -> agent version, plus a candidate."""
    agent = AgentVersion(
        name="screener", language="ENGLISH", voice_persona="NEHA",
        persona_name="Neha", agent_prompt="p", objective="o",
        introduction="i", result_prompt="r",
    )
    session.add(agent)
    await session.flush()

    campaign = Campaign(kind=CampaignKind.SCREENING, name="c", agent_version_id=agent.id)
    candidate = Candidate(
        name="Rider", phone_e164="+919000000001",
        source=CandidateSource.INBOUND_CSV, dedupe_key="dk-1",
    )
    session.add_all([campaign, candidate])
    await session.flush()

    call = Call(
        campaign_id=campaign.id,
        candidate_id=candidate.id,
        hunar_call_id=HUNAR_CALL_ID,
        **overrides,
    )
    session.add(call)
    await session.commit()
    return call


def event_body(**fields) -> str:
    return json.dumps({"call_id": HUNAR_CALL_ID, **fields})


async def store_event(session, body: str, event_type: str = "call_status_updated") -> CallEvent:
    import hashlib

    event = CallEvent(
        hunar_call_id=HUNAR_CALL_ID,
        event_type=event_type,
        raw_body=body,
        payload_hash=hashlib.sha256(body.encode()).hexdigest(),
    )
    session.add(event)
    await session.commit()
    return event


# --- ranking ---------------------------------------------------------------


async def test_retry_applies_even_though_status_moves_backwards(session) -> None:
    """The case that breaks a naive status-only ranking: attempt 1 completes,
    then the retry starts ringing. RINGING ranks below COMPLETED, but it is a
    later attempt, so it must win."""
    call = await make_call(session, status="COMPLETED", retry_count=0)

    changed = apply_call_update(
        call, CallUpdate(HUNAR_CALL_ID, status="RINGING", retry_count=1)
    )

    assert changed is True
    assert call.status == "RINGING"
    assert call.retry_count == 1


async def test_stale_event_within_the_same_attempt_is_dropped(session) -> None:
    call = await make_call(session, status="COMPLETED", retry_count=0)

    changed = apply_call_update(
        call, CallUpdate(HUNAR_CALL_ID, status="RINGING", retry_count=0)
    )

    assert changed is False
    assert call.status == "COMPLETED"


async def test_lifecycle_never_regresses_from_terminal(session) -> None:
    call = await make_call(session, status="COMPLETED", lifecycle_status="COMPLETED")

    apply_call_update(
        call,
        CallUpdate(HUNAR_CALL_ID, status="RINGING", lifecycle_status="IN_PROGRESS", retry_count=1),
    )

    assert call.lifecycle_status == "COMPLETED"
    assert call.status == "RINGING", "the attempt-level status should still advance"


async def test_unknown_status_is_recorded_not_discarded(session) -> None:
    call = await make_call(session, status="RINGING", retry_count=0)

    changed = apply_call_update(
        call, CallUpdate(HUNAR_CALL_ID, status="VOICEMAIL_DROPPED", retry_count=0)
    )

    assert changed is True
    assert call.status == "VOICEMAIL_DROPPED"


# --- additive fields -------------------------------------------------------


async def test_result_survives_a_later_event_that_omits_it(session) -> None:
    """The result arrives on its own event; the status stream keeps flowing
    afterwards with the field absent, and must not wipe it."""
    call = await make_call(session, status="IN_PROGRESS", retry_count=0)

    apply_call_update(
        call,
        CallUpdate(HUNAR_CALL_ID, result={"open_to_work": True}, recording_url="https://r/1.mp3"),
    )
    apply_call_update(
        call, CallUpdate(HUNAR_CALL_ID, status="COMPLETED", retry_count=0)
    )

    assert call.result == {"open_to_work": True}
    assert call.recording_url == "https://r/1.mp3"


# --- orchestration ---------------------------------------------------------


async def test_event_for_unknown_call_waits_for_orphan_resolution(session) -> None:
    """The webhook beat our own dispatch write."""
    event = await store_event(session, event_body(status="RINGING", retry_count=0))

    await process_call_event(event.id)

    await session.refresh(event)
    assert event.processed_at is None, "must stay unprocessed, not be discarded"
    assert event.call_id is None

    # Dispatch lands, and task 10 calls this.
    call = await make_call(session)
    applied = await resolve_orphan_events(session, HUNAR_CALL_ID)

    assert applied == 1
    await session.refresh(call)
    await session.refresh(event)
    assert call.status == "RINGING"
    assert event.processed_at is not None
    assert event.call_id == call.id


async def test_processing_the_same_event_twice_changes_state_once(session) -> None:
    """A background task re-run after a restart must not re-apply the event.

    Proven by moving the call on between the two runs: if the second run touched
    the record at all it would drag the status back to the event's value.
    """
    call = await make_call(session, status="RINGING", retry_count=0)
    event = await store_event(session, event_body(status="COMPLETED", retry_count=0))

    await process_call_event(event.id)
    await session.refresh(call)
    assert call.status == "COMPLETED"

    # Rewind the call to a state the event WOULD legitimately advance, so the
    # only thing that can stop the second run is the processed_at guard itself.
    # Without this the staleness rule alone would pass the test and the guard
    # could be deleted unnoticed.
    call.status = "RINGING"
    await session.commit()

    await process_call_event(event.id)
    await session.refresh(call)

    assert call.status == "RINGING", "an already-processed event was applied again"


async def test_unparseable_payload_is_marked_errored_and_does_not_raise(session) -> None:
    await make_call(session)
    event = await store_event(session, "<html>502 Bad Gateway</html>")

    await process_call_event(event.id)  # must not raise

    stored = (
        await session.execute(select(CallEvent).where(CallEvent.id == event.id))
    ).scalar_one()
    await session.refresh(stored)
    assert stored.processed_at is not None
    assert stored.processing_error == "could not parse payload"


async def test_empty_result_does_not_erase_a_real_one(session) -> None:
    """Hunar sends `"result": {}` on a call that produced none. Observed in the
    task 3.5 capture; without this the summary event wipes a real result."""
    call = await make_call(session, status="COMPLETED", retry_count=0)

    apply_call_update(call, CallUpdate(HUNAR_CALL_ID, result={"open_to_work": True}))
    apply_call_update(call, CallUpdate(HUNAR_CALL_ID, result={}))

    assert call.result == {"open_to_work": True}
