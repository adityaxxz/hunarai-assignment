"""Campaign dispatch and read. The endpoint that spends money."""

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.integrations.hunar.errors import HunarError, HunarQuotaError, HunarValidationError
from app.integrations.hunar.provider import get_voice_provider
from app.integrations.hunar.types import BulkCallCreate, BulkCallRecipient, CallbackConfig
from app.models import (
    AgentVersion,
    Call,
    Campaign,
    CampaignKind,
    CampaignStatus,
    Candidate,
    CandidateStatus,
    Requisition,
)
from app.schemas import CallRead, CampaignCreate, CampaignDetail, CampaignPage
from app.services.campaign import (
    CampaignValidationError,
    estimate,
    next_dial_start,
    validate_campaign,
)
from app.services.call_state import resolve_orphan_events
from app.services.reconcile import reconcile_for_view

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# Every funnel stage a call can be in, in order. Derived from the two Hunar
# status fields rather than stored, so it cannot drift from them.
FUNNEL_STAGES = (
    "queued", "dialling", "connected", "engaged", "completed",
    "not_connected", "failed", "dispatch_failed",
)


def funnel_stage(call: Call) -> str:
    if call.dispatch_error:
        return "dispatch_failed"
    if call.hunar_call_id is None or call.status in (None, "NOT_STARTED", "SCHEDULED"):
        return "queued"
    if call.lifecycle_status == "NOT_CONNECTED":
        return "not_connected"
    if call.lifecycle_status == "FAILED":
        return "failed"
    if call.lifecycle_status == "COMPLETED":
        # Engagement is API-only and arrives by reconciliation, so a completed
        # call with no engagement yet is reported as completed rather than
        # guessed into engaged.
        return "engaged" if call.engagement_status == "ENGAGED" else "completed"
    if call.status == "IN_PROGRESS":
        return "connected"
    return "dialling"


@router.post("", response_model=CampaignDetail, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate, session: AsyncSession = Depends(get_session)
) -> CampaignDetail:
    """Validate, write rows, dispatch, write ids back.

    The order is the design. Call rows exist **before** the dispatch request, so:
      * a webhook that beats our write finds a row to attach to, and
      * a crash between dispatch and write-back leaves rows we can recover from
        rather than calls Hunar is running that we have no record of.
    """
    requisition = await session.get(Requisition, payload.requisition_id)
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")

    try:
        validate_campaign(
            payload.guardrails.model_dump() if payload.guardrails else None,
            payload.retry_config.model_dump() if payload.retry_config else None,
        )
    except CampaignValidationError as exc:
        raise HTTPException(status_code=422, detail={"problems": exc.problems}) from exc

    agent = await _agent_version(session, requisition.id, payload.agent_version_id)
    dialable, blocked = await _split_candidates(session, requisition)

    if blocked:
        # Refuse rather than dispatch and collect 422s per row. Hunar rejects a
        # call whose custom_data lacks a declared key, so these would fail one at
        # a time after the campaign had already started.
        raise HTTPException(
            status_code=422,
            detail={
                "problems": ["some candidates are missing declared variables"],
                "candidates": blocked,
            },
        )
    if not dialable:
        raise HTTPException(status_code=422, detail={"problems": ["no candidate is dialable"]})

    campaign = Campaign(
        kind=CampaignKind.SCREENING,
        name=payload.name,
        requisition_id=requisition.id,
        agent_version_id=agent.id,
        timezone=payload.timezone,
        from_phone_number=payload.from_phone_number,
        status=CampaignStatus.DISPATCHING,
        max_retry_count=payload.retry_config.max_retry_count if payload.retry_config else None,
        retry_interval_hours=payload.retry_config.retry_interval_hours if payload.retry_config else None,
        allowed_days=payload.guardrails.allowed_days if payload.guardrails else None,
        earliest_call_time=payload.guardrails.earliest_call_time if payload.guardrails else None,
        last_call_time=payload.guardrails.last_call_time if payload.guardrails else None,
    )
    session.add(campaign)
    await session.flush()
    campaign.request_id = f"ARFDE-{campaign.id}"

    calls = [
        Call(campaign_id=campaign.id, candidate_id=candidate.id, hunar_call_id=None)
        for candidate in dialable
    ]
    session.add_all(calls)
    # Committed before the dispatch request goes out. This is the whole point.
    await session.commit()

    by_phone = {c.phone_e164: call for c, call in zip(dialable, calls, strict=True)}
    await _dispatch(session, campaign, agent, dialable, by_phone)
    await session.commit()

    # Webhooks can and do arrive before the write-back lands. Anything stored
    # unlinked is applied now that the rows have ids.
    for call in calls:
        if call.hunar_call_id:
            await resolve_orphan_events(session, call.hunar_call_id)

    return await _detail(session, campaign)


async def _dispatch(
    session: AsyncSession,
    campaign: Campaign,
    agent: AgentVersion,
    candidates: list[Candidate],
    by_phone: dict[str, Call],
) -> None:
    base = settings.public_base_url.rstrip("/")
    payload = BulkCallCreate(
        agent_id=agent.hunar_agent_id or "",
        request_id=campaign.request_id,
        from_phone_number=campaign.from_phone_number,
        timezone=campaign.timezone,
        data=[
            BulkCallRecipient(
                callee_name=c.name,
                mobile_number=c.phone_e164,
                custom_data=c.custom_fields or None,
            )
            for c in candidates
        ],
        retry_config=(
            {"max_retry_count": campaign.max_retry_count,
             "retry_interval_hours": campaign.retry_interval_hours}
            if campaign.max_retry_count is not None
            else None
        ),
        guardrails=(
            {"allowed_days": campaign.allowed_days,
             "earliest_call_time": campaign.earliest_call_time,
             "last_call_time": campaign.last_call_time}
            if campaign.allowed_days
            else None
        ),
        callback_config=CallbackConfig(
            call_status_callback_url=f"{base}/webhooks/hunar/call_status_updated",
            call_recording_callback_url=f"{base}/webhooks/hunar/call_recording_done",
            call_result_callback_url=f"{base}/webhooks/hunar/call_result_done",
            call_summary_callback_url=f"{base}/webhooks/hunar/call_summary",
        ),
    )

    try:
        # A bare JSON array, not the paginated envelope every other list returns.
        accepted = await get_voice_provider().create_bulk_calls(payload)
    except (HunarQuotaError, HunarValidationError, HunarError) as exc:
        # Nothing left. Every row is marked so the campaign cannot claim to be
        # running when not one call was placed.
        campaign.status = CampaignStatus.FAILED
        campaign.dispatch_error = str(exc)
        for call in by_phone.values():
            call.dispatch_error = str(exc)
        logger.warning("campaign %s dispatch failed: %s", campaign.id, exc)
        return

    for api_call in accepted:
        call = by_phone.get(api_call.mobile_number or "")
        if call is None:
            logger.warning(
                "campaign %s: Hunar returned a call for a number we did not send",
                campaign.id,
            )
            continue
        call.hunar_call_id = api_call.id

    # Hunar drops invalid and duplicate rows silently by default, so anything
    # without an id came back rejected and must say so rather than sit as queued.
    rejected = [c for c in by_phone.values() if c.hunar_call_id is None]
    for call in rejected:
        call.dispatch_error = "Hunar did not accept this number in the batch"

    campaign.dispatched_at = datetime.now(timezone.utc)
    if not rejected:
        campaign.status = CampaignStatus.RUNNING
    elif len(rejected) == len(by_phone):
        campaign.status = CampaignStatus.FAILED
        campaign.dispatch_error = "Hunar accepted none of the numbers in the batch"
    else:
        campaign.status = CampaignStatus.PARTIALLY_DISPATCHED
        campaign.dispatch_error = f"{len(rejected)} of {len(by_phone)} numbers were not accepted"


@router.get("/{campaign_id}", response_model=CampaignDetail)
async def get_campaign(
    campaign_id: int, session: AsyncSession = Depends(get_session)
) -> CampaignDetail:
    campaign = await _campaign(session, campaign_id)
    # Reconciliation before returning: Hunar sends one status webhook per call,
    # at the terminal transition, so without this the funnel would not move at
    # all while someone watched it.
    await reconcile_for_view(session, campaign_id)
    return await _detail(session, campaign)


@router.get("/{campaign_id}/calls", response_model=CampaignPage)
async def list_calls(
    campaign_id: int,
    stage: str | None = Query(default=None, description="funnel stage"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> CampaignPage:
    await _campaign(session, campaign_id)
    await reconcile_for_view(session, campaign_id)

    rows = (
        await session.execute(
            select(Call, Candidate)
            .join(Candidate, Call.candidate_id == Candidate.id)
            .where(Call.campaign_id == campaign_id)
            .order_by(Call.id)
        )
    ).all()
    items = [_call_read(call, candidate) for call, candidate in rows]
    if stage:
        items = [i for i in items if i.stage == stage]

    start = (page - 1) * page_size
    return CampaignPage(
        total=len(items), page=page, page_size=page_size,
        results=items[start : start + page_size],
    )


async def _campaign(session: AsyncSession, campaign_id: int) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


async def _agent_version(
    session: AsyncSession, requisition_id: int, agent_version_id: int | None
) -> AgentVersion:
    stmt = select(AgentVersion).where(
        AgentVersion.requisition_id == requisition_id,
        AgentVersion.hunar_agent_id.is_not(None),
    )
    if agent_version_id is not None:
        stmt = stmt.where(AgentVersion.id == agent_version_id)
    agent = (await session.execute(stmt.order_by(AgentVersion.version.desc()))).scalars().first()
    if agent is None:
        raise HTTPException(
            status_code=422,
            detail={"problems": ["this requisition has no agent pushed to Hunar yet"]},
        )
    return agent


async def _split_candidates(
    session: AsyncSession, requisition: Requisition
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    candidates = (
        await session.execute(
            select(Candidate).where(Candidate.requisition_id == requisition.id)
        )
    ).scalars().all()

    dialable, blocked = [], []
    for candidate in candidates:
        if candidate.status is CandidateStatus.DO_NOT_CALL:
            continue  # excluded silently here; preflight already reports why
        missing = [
            v for v in requisition.candidate_variables if not candidate.custom_fields.get(v)
        ]
        if missing:
            blocked.append(
                {"candidate_id": candidate.id, "name": candidate.name, "missing": missing}
            )
        else:
            dialable.append(candidate)
    return dialable, blocked


def _call_read(call: Call, candidate: Candidate) -> CallRead:
    return CallRead(
        id=call.id,
        candidate_id=candidate.id,
        candidate_name=candidate.name,
        hunar_call_id=call.hunar_call_id,
        stage=funnel_stage(call),
        status=call.status,
        lifecycle_status=call.lifecycle_status,
        engagement_status=call.engagement_status,
        retry_count=call.retry_count,
        retries_left=call.retries_left,
        next_retry_scheduled_at=call.next_retry_scheduled_at,
        dispatch_error=call.dispatch_error,
        has_result=bool(call.result),
    )


async def _detail(session: AsyncSession, campaign: Campaign) -> CampaignDetail:
    rows = (
        await session.execute(
            select(Call).where(Call.campaign_id == campaign.id)
        )
    ).scalars().all()

    funnel = {stage: 0 for stage in FUNNEL_STAGES}
    for call in rows:
        funnel[funnel_stage(call)] += 1

    guardrails = (
        {"allowed_days": campaign.allowed_days,
         "earliest_call_time": campaign.earliest_call_time,
         "last_call_time": campaign.last_call_time}
        if campaign.allowed_days
        else None
    )
    window = next_dial_start(guardrails, campaign.timezone)
    retry = (
        {"max_retry_count": campaign.max_retry_count,
         "retry_interval_hours": campaign.retry_interval_hours}
        if campaign.max_retry_count is not None
        else None
    )

    return CampaignDetail(
        id=campaign.id,
        name=campaign.name,
        requisition_id=campaign.requisition_id,
        agent_version_id=campaign.agent_version_id,
        request_id=campaign.request_id,
        status=campaign.status,
        dispatch_error=campaign.dispatch_error,
        dispatched_at=campaign.dispatched_at,
        total_calls=len(rows),
        funnel=funnel,
        dialling_now=window.dialling_now,
        dial_starts_at=window.starts_at,
        dial_window_note=window.explanation,
        estimate=estimate(len(rows), retry),
    )
