"""Call state machine: decide whether an update is newer than what we hold, and
apply it without losing information.

Everything here works on `CallUpdate`, which carries values already pulled out of
a payload. Nothing in this file knows what Hunar's JSON looks like; that is
`call_event_parser.py`, deliberately isolated so the shape can be corrected in
one place once the live fixtures land.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Call, CallEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallUpdate:
    """One call's worth of already-extracted values. The boundary between parsing
    and state: everything above this line is JSON, everything below is typed."""

    hunar_call_id: str
    status: str | None = None
    lifecycle_status: str | None = None
    engagement_status: str | None = None
    answered_by: str | None = None
    call_ended_by: str | None = None
    redial_status: str | None = None
    retry_count: int | None = None
    retries_left: int | None = None
    next_retry_scheduled_at: datetime | None = None
    recording_url: str | None = None
    result: dict[str, Any] | None = None
    duration_seconds: float | None = None
    user_speech_duration: float | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


# Two axes, and conflating them is the bug this module exists to prevent.
#
# `lifecycle_status` is genuinely monotonic. It describes the call overall and
# never moves backwards: NOT_STARTED, then IN_PROGRESS, then a terminal value.
# All four terminal values rank equal, and terminal is a one-way door.
#
# `status` is NOT monotonic. It describes the *current attempt*, so a call that
# reaches COMPLETED or NOT_CONNECTED on attempt one legitimately goes back to
# SCHEDULED and RINGING on the retry. Ranking status on its own would classify
# every retry update as stale and freeze the funnel on the first attempt.
#
# So the ordering key is the tuple (retry_count, status_rank), and comparisons
# are between tuples, never between statuses. That is the whole point of the
# module: retry_count dominates, and status only breaks ties within one attempt.
_STATUS_RANK = {
    "NOT_STARTED": 0,
    "SCHEDULED": 1,
    "INITIATED": 2,
    "RINGING": 3,
    "IN_PROGRESS": 4,
    "COMPLETED": 5,
    "NOT_CONNECTED": 5,
    "CANCELLED": 5,
    "FAILED": 5,
}

_LIFECYCLE_RANK = {
    "NOT_STARTED": 0,
    "IN_PROGRESS": 1,
    "COMPLETED": 2,
    "NOT_CONNECTED": 2,
    "FAILED": 2,
    "CANCELLED": 2,
}


def should_apply(call: Call, update: CallUpdate) -> bool:
    """False when the update is older than what the record already holds.

    A missing `retry_count` on either side is read as the value already on the
    record, not as attempt zero: a payload that simply omits the field must not
    look like it rewound the call to its first attempt.

    An unrecognised status ranks *equal to the current one* rather than lowest,
    so a value Hunar adds later is treated as at least as new and gets recorded
    instead of silently discarded.
    """
    existing_retry = call.retry_count or 0
    incoming_retry = (
        update.retry_count if update.retry_count is not None else existing_retry
    )

    existing_rank = _STATUS_RANK.get(call.status or "", -1)
    if update.status is None:
        incoming_rank = existing_rank
    else:
        incoming_rank = _STATUS_RANK.get(update.status, existing_rank)

    return (incoming_retry, incoming_rank) >= (existing_retry, existing_rank)


def _should_advance_lifecycle(current: str | None, incoming: str | None) -> bool:
    if incoming is None:
        return False
    if current is None:
        return True
    incoming_rank = _LIFECYCLE_RANK.get(incoming)
    current_rank = _LIFECYCLE_RANK.get(current)
    # Same tolerance as status: a vocabulary we do not know yet is recorded
    # rather than dropped, because the alternative is losing the only evidence
    # that Hunar changed something.
    if incoming_rank is None or current_rank is None:
        return True
    return incoming_rank > current_rank


def apply_call_update(call: Call, update: CallUpdate) -> bool:
    """Merge `update` into `call`. Returns whether anything actually changed.

    Deliberately does not touch `decision`, `score` or the override columns.
    Turning a result into a qualification verdict is `evaluation.py` in task 8;
    doing it here would mean a redelivered webhook silently re-decides a
    candidate a recruiter has already overridden.
    """
    changed = False

    # Additive, and checked before the staleness gate on purpose. The recording
    # and the result arrive on their own events, out of band from the status
    # stream, and later events re-send the call with those fields absent. Once
    # set, they are never cleared by a payload that simply did not carry them.
    if update.recording_url and call.recording_url != update.recording_url:
        call.recording_url = update.recording_url
        changed = True
    # Truthiness, not `is not None`: the live capture shows Hunar sends
    # `"result": {}` on a call that produced none, and an empty result is not a
    # result. Testing for None would let a summary event overwrite a real result
    # with an empty dict.
    if update.result and call.result != update.result:
        call.result = update.result
        changed = True

    # Its own rule, independent of the status tuple: overall state only advances.
    if _should_advance_lifecycle(call.lifecycle_status, update.lifecycle_status):
        if call.lifecycle_status != update.lifecycle_status:
            changed = True
        call.lifecycle_status = update.lifecycle_status

    if not should_apply(call, update):
        return changed

    for field in (
        "status",
        "engagement_status",
        "answered_by",
        "call_ended_by",
        "redial_status",
        "retry_count",
        "retries_left",
        "next_retry_scheduled_at",
        "duration_seconds",
        "user_speech_duration",
        "started_at",
        "ended_at",
    ):
        incoming = getattr(update, field)
        if incoming is not None and getattr(call, field) != incoming:
            setattr(call, field, incoming)
            changed = True

    return changed


async def resolve_orphan_events(session: AsyncSession, hunar_call_id: str) -> int:
    """Apply events that arrived before the call row existed. Returns how many.

    Task 10 calls this immediately after writing call rows at dispatch, because a
    webhook can and does beat our own INSERT: the receiver stores those events
    unlinked rather than rejecting them, and this is what picks them back up.
    """
    # Imported here rather than at module scope: the parser needs `CallUpdate`
    # from this module, so a top-level import in either direction is a cycle.
    # The dependency genuinely points both ways and this is the cheaper break.
    from app.services.call_event_parser import parse_event

    call = await _load_call(session, hunar_call_id)
    if call is None:
        return 0

    events = (
        (
            await session.execute(
                select(CallEvent)
                .where(
                    CallEvent.hunar_call_id == hunar_call_id,
                    CallEvent.processed_at.is_(None),
                )
                .order_by(CallEvent.received_at, CallEvent.id)
            )
        )
        .scalars()
        .all()
    )

    applied = 0
    for event in events:
        update = parse_event(event.event_type, event.raw_body)
        if update is None:
            _mark_processed(event, error="could not parse payload")
            continue
        apply_call_update(call, update)
        event.call_id = call.id
        _mark_processed(event)
        applied += 1

    if events:
        await session.commit()
    return applied


async def process_call_event(event_id: int) -> None:
    """Interpret one stored webhook event and move the call record forward.

    Runs as a FastAPI background task, after the response has gone out and the
    request's session is closed, so it opens its own.
    """
    from app.services.call_event_parser import parse_event

    async with SessionLocal() as session:
        event = await session.get(CallEvent, event_id)
        if event is None:
            logger.warning("call event %s vanished before processing", event_id)
            return

        # Makes the task safe to re-run: a background task retried after a
        # restart, or an event also swept up by orphan resolution, is a no-op.
        if event.processed_at is not None:
            return

        update = parse_event(event.event_type, event.raw_body)
        if update is None:
            # Recorded, not raised. One payload we cannot read must not wedge the
            # pipeline, and the row keeps the raw body for a human to look at.
            logger.warning(
                "call event %s (%s) could not be parsed, marking errored",
                event_id,
                event.event_type,
            )
            _mark_processed(event, error="could not parse payload")
            await session.commit()
            return

        call = await _load_call(session, update.hunar_call_id)
        if call is None:
            # Left deliberately unprocessed. The dispatch write has not landed
            # yet; resolve_orphan_events picks this up once it does. Marking it
            # processed here would discard the event for good.
            logger.info(
                "call event %s references unknown call, leaving for orphan resolution",
                event_id,
            )
            return

        apply_call_update(call, update)
        event.call_id = call.id
        _mark_processed(event)
        await session.commit()


async def _load_call(session: AsyncSession, hunar_call_id: str) -> Call | None:
    return (
        await session.execute(select(Call).where(Call.hunar_call_id == hunar_call_id))
    ).scalar_one_or_none()


def _mark_processed(event: CallEvent, error: str | None = None) -> None:
    event.processed_at = datetime.now(timezone.utc)
    event.processing_error = error
