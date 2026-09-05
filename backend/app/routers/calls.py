"""One call, in full: the rubric, the decision, the recording and the timeline.

This is the screen a recruiter actually makes a hiring judgement on, so it is
built to be arguable rather than authoritative. It shows what the candidate said
per criterion, why each one passed or failed, and where a human overruled the
machine — with the machine's own conclusion still visible beside it.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import AuditLog, Call, CallEvent, Campaign, Candidate, Requisition, ScreeningDecision
from app.schemas import (
    OVERRIDE_REASON_LABELS,
    CallDetail,
    CallEventRead,
    EvaluationRead,
    OverrideCreate,
    OverrideRead,
)
from app.services.evaluation import evaluate
from app.services.recording import RecordingUnavailable, silent_wav, stream_recording
from app.services.reconcile import reconcile_for_view
from app.routers.campaigns import funnel_stage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])

# No authentication exists in this build, so an override is attributed to a
# constant rather than to a fabricated user id. Recording "recruiter" honestly is
# better than recording a name we invented. See current-status.md.
OVERRIDE_ACTOR = "recruiter"


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: int, session: AsyncSession = Depends(get_session)
) -> CallDetail:
    call, candidate, requisition = await _load(session, call_id)
    # Same rate-limited reconcile the funnel uses. Opening one candidate is
    # exactly when a stale result is most annoying, and the 5s per-campaign limit
    # keeps a refresh-happy recruiter from becoming a load test.
    await reconcile_for_view(session, call.campaign_id)
    await session.refresh(call)
    return await _detail(session, call, candidate, requisition)


@router.get("/{call_id}/recording")
async def get_recording(call_id: int, session: AsyncSession = Depends(get_session)):
    """Proxy the audio. The S3 URL never leaves the backend.

    Not a redirect: a 302 to the S3 URL would put it in the browser's history,
    the network tab and any copied link, which is the exact exposure the proxy
    exists to prevent.
    """
    call, _, _ = await _load(session, call_id)

    if settings.demo_mode:
        # Generated silence rather than a 404. The player, its duration and the
        # "simulated" label are all real; only the audio is not.
        return Response(
            content=silent_wav(),
            media_type="audio/wav",
            headers={"X-Recording-Source": "simulated"},
        )

    if not call.recording_url:
        raise HTTPException(
            status_code=404,
            detail="No recording for this call yet. It may still be uploading, or the call may never have connected.",
        )

    try:
        # Consumed once here so an upstream failure becomes a clean error
        # response, instead of a 200 whose body dies halfway through.
        stream = stream_recording(call.recording_url)
        first = await anext(stream, b"")
    except RecordingUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def body():
        yield first
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        body(),
        media_type="audio/wav",
        headers={"X-Recording-Source": "hunar"},
    )


@router.post("/{call_id}/override", response_model=CallDetail)
async def override_decision(
    call_id: int,
    payload: OverrideCreate,
    session: AsyncSession = Depends(get_session),
) -> CallDetail:
    """Record a human decision over the computed one.

    The computed decision is never modified. It is derived on read from the
    result and the criteria, so overwriting it would destroy the only thing worth
    auditing: what the machine concluded before a person disagreed.
    """
    call, candidate, requisition = await _load(session, call_id)
    computed = evaluate(call.result, requisition) if requisition else None
    now = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            entity_type="call",
            entity_id=call.id,
            action="override_decision",
            actor=OVERRIDE_ACTOR,
            reason_code=payload.reason_code,
            note=payload.note,
            before={
                "computed_decision": computed.decision.value if computed else None,
                "computed_score": computed.score if computed else None,
                # The previous override, where this is a second correction.
                "override_decision": call.override_decision.value
                if call.override_decision
                else None,
            },
            after={"override_decision": payload.decision},
        )
    )

    call.override_decision = ScreeningDecision(payload.decision)
    call.override_reason_code = payload.reason_code
    call.override_note = payload.note
    call.overridden_at = now
    await session.commit()

    return await _detail(session, call, candidate, requisition)


async def _load(
    session: AsyncSession, call_id: int
) -> tuple[Call, Candidate, Requisition | None]:
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")

    candidate = await session.get(Candidate, call.candidate_id)
    if candidate is None:
        # Only reachable if a candidate row were deleted out from under a call,
        # which nothing in this build does.
        raise HTTPException(status_code=404, detail="Candidate not found for this call")

    campaign = await session.get(Campaign, call.campaign_id)
    requisition = (
        await session.get(Requisition, campaign.requisition_id)
        if campaign and campaign.requisition_id
        else None
    )
    return call, candidate, requisition


async def _timeline(session: AsyncSession, call: Call) -> list[CallEventRead]:
    """Every event for this call, including ones that arrived before the row.

    Matched on `hunar_call_id` as well as the foreign key, because an event that
    landed early is stored unlinked and only adopted later. Excluding those would
    hide the orphan path at precisely the moment it is interesting.
    """
    where = CallEvent.call_id == call.id
    if call.hunar_call_id:
        where = or_(where, CallEvent.hunar_call_id == call.hunar_call_id)

    rows = (
        await session.execute(
            select(CallEvent).where(where).order_by(CallEvent.received_at, CallEvent.id)
        )
    ).scalars().all()

    return [
        CallEventRead(
            id=event.id,
            event_type=event.event_type,
            received_at=event.received_at,
            processed_at=event.processed_at,
            processing_error=event.processing_error,
            linked=event.call_id is not None,
        )
        for event in rows
    ]


async def _detail(
    session: AsyncSession,
    call: Call,
    candidate: Candidate,
    requisition: Requisition | None,
) -> CallDetail:
    computed = evaluate(call.result, requisition) if requisition else None

    override = (
        OverrideRead(
            decision=call.override_decision.value,
            reason_code=call.override_reason_code or "",
            reason_label=OVERRIDE_REASON_LABELS.get(
                call.override_reason_code or "", call.override_reason_code or ""
            ),
            note=call.override_note,
            at=call.overridden_at,
        )
        if call.override_decision and call.overridden_at
        else None
    )

    return CallDetail(
        id=call.id,
        campaign_id=call.campaign_id,
        hunar_call_id=call.hunar_call_id,
        candidate_id=candidate.id,
        candidate_name=candidate.name,
        candidate_phone=candidate.phone_e164,
        candidate_custom_fields=candidate.custom_fields,
        requisition_id=requisition.id if requisition else None,
        requisition_title=requisition.title if requisition else None,
        stage=funnel_stage(call),
        status=call.status,
        lifecycle_status=call.lifecycle_status,
        engagement_status=call.engagement_status,
        answered_by=call.answered_by,
        call_ended_by=call.call_ended_by,
        redial_status=call.redial_status,
        retry_count=call.retry_count,
        retries_left=call.retries_left,
        next_retry_scheduled_at=call.next_retry_scheduled_at,
        duration_seconds=call.duration_seconds,
        user_speech_duration=call.user_speech_duration,
        started_at=call.started_at,
        ended_at=call.ended_at,
        dispatch_error=call.dispatch_error,
        last_reconciled_at=call.last_reconciled_at,
        reconcile_stopped_at=call.reconcile_stopped_at,
        reconcile_stopped_reason=call.reconcile_stopped_reason,
        result=call.result,
        evaluation=(
            EvaluationRead(
                decision=computed.decision.value,
                score=computed.score,
                reasons=[o.__dict__ for o in computed.reasons],
            )
            if computed
            else None
        ),
        override=override,
        effective_decision=(
            override.decision if override else (computed.decision.value if computed else None)
        ),
        # In demo mode the proxy always has something to serve, so the player is
        # offered whenever the simulator says a recording exists.
        recording_available=bool(call.recording_url),
        recording_simulated=settings.demo_mode,
        timeline=await _timeline(session, call),
    )
