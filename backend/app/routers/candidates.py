"""Candidate intake and listing."""

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import (
    AgentVersion,
    Candidate,
    CandidateSource,
    CandidateStatus,
    DncEntry,
    Requisition,
)
from app.schemas import (
    CandidateManualCreate,
    CandidatePage,
    CandidateRead,
    MappingProposal,
    PreflightReport,
)
from app.services.candidate_intake import (
    build_plan,
    dedupe_key,
    propose_mapping,
    read_csv,
)

router = APIRouter(prefix="/requisitions/{requisition_id}", tags=["candidates"])

PREVIEW_ROWS = 5


async def _requisition(session: AsyncSession, requisition_id: int) -> Requisition:
    requisition = await session.get(Requisition, requisition_id)
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return requisition


async def _existing_phones(session: AsyncSession, requisition_id: int) -> set[str]:
    rows = await session.execute(
        select(Candidate.phone_e164).where(Candidate.requisition_id == requisition_id)
    )
    return set(rows.scalars())


async def _dnc_phones(session: AsyncSession) -> set[str]:
    return set((await session.execute(select(DncEntry.phone_e164))).scalars())


@router.post("/candidates/upload", response_model=MappingProposal)
async def upload_candidates(
    requisition_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> MappingProposal:
    """Propose a mapping. Writes nothing.

    Deliberately two steps. A silently mis-mapped column dials the wrong people,
    and that is invisible in "imported 400 candidates" but obvious in a preview
    showing the name column full of phone numbers. The recruiter confirms what
    each column means before anything is stored.
    """
    requisition = await _requisition(session, requisition_id)
    headers, rows = read_csv(await file.read())
    if not headers:
        raise HTTPException(status_code=422, detail="That file has no header row")

    return MappingProposal(
        headers=headers,
        row_count=len(rows),
        mapping=propose_mapping(headers, requisition.candidate_variables),
        required_variables=list(requisition.candidate_variables),
        preview=rows[:PREVIEW_ROWS],
    )


@router.post("/candidates/import")
async def import_candidates(
    requisition_id: int,
    file: UploadFile = File(...),
    mapping: str = Form(..., description="JSON object from the upload step, corrected"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    requisition = await _requisition(session, requisition_id)
    try:
        confirmed: dict[str, str | None] = json.loads(mapping)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="mapping is not valid JSON") from exc

    headers, rows = read_csv(await file.read())
    plan = build_plan(
        rows,
        confirmed,
        requisition.candidate_variables,
        await _existing_phones(session, requisition_id),
        await _dnc_phones(session),
    )
    _persist(session, plan.importable, requisition_id, CandidateSource.INBOUND_CSV)
    await session.commit()
    return plan.summary()


@router.post("/candidates", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
async def add_candidate(
    requisition_id: int,
    payload: CandidateManualCreate,
    session: AsyncSession = Depends(get_session),
) -> CandidateRead:
    """One candidate by hand, through the identical validation path.

    Built as a one-row CSV rather than a second set of rules, because two
    implementations of "is this phone number usable" drift and the manual path is
    the one nobody tests.
    """
    requisition = await _requisition(session, requisition_id)
    row = {"name": payload.name, "phone": payload.phone, **payload.custom_fields}
    mapping: dict[str, str | None] = {"name": "name", "phone": "phone"}
    for variable in requisition.candidate_variables:
        mapping[variable] = variable

    plan = build_plan(
        [row],
        mapping,
        requisition.candidate_variables,
        await _existing_phones(session, requisition_id),
        await _dnc_phones(session),
        first_row_number=1,
    )
    result = plan.rows[0]
    if not result.importable:
        raise HTTPException(status_code=422, detail={"reasons": result.reasons})

    created = _persist(session, [result], requisition_id, CandidateSource.MANUAL)[0]
    await session.commit()
    return CandidateRead.model_validate(created, from_attributes=True)


@router.get("/candidates", response_model=CandidatePage)
async def list_candidates(
    requisition_id: int,
    candidate_status: CandidateStatus | None = Query(default=None, alias="status"),
    source: CandidateSource | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> CandidatePage:
    await _requisition(session, requisition_id)
    stmt = select(Candidate).where(Candidate.requisition_id == requisition_id)
    if candidate_status is not None:
        stmt = stmt.where(Candidate.status == candidate_status)
    if source is not None:
        stmt = stmt.where(Candidate.source == source)

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(Candidate.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    return CandidatePage(
        total=total,
        page=page,
        page_size=page_size,
        results=[CandidateRead.model_validate(r, from_attributes=True) for r in rows],
    )


@router.get("/preflight", response_model=PreflightReport)
async def preflight(
    requisition_id: int, session: AsyncSession = Depends(get_session)
) -> PreflightReport:
    """What would actually happen if a campaign launched right now.

    This is the screen shown before money is spent, so it reports what will fail
    rather than what should work. Every exclusion is counted and named.
    """
    requisition = await _requisition(session, requisition_id)
    candidates = (
        await session.execute(
            select(Candidate).where(Candidate.requisition_id == requisition_id)
        )
    ).scalars().all()

    agent = (
        await session.execute(
            select(AgentVersion)
            .where(AgentVersion.requisition_id == requisition_id)
            .where(AgentVersion.hunar_agent_id.is_not(None))
            .order_by(AgentVersion.version.desc())
        )
    ).scalars().first()

    dialable = 0
    excluded: list[dict[str, Any]] = []
    for candidate in candidates:
        reasons: list[str] = []
        if candidate.status is CandidateStatus.DO_NOT_CALL:
            reasons.append("on the do-not-call list")
        missing = [
            v for v in requisition.candidate_variables if not candidate.custom_fields.get(v)
        ]
        if missing:
            # Not a warning: Hunar rejects the call outright, so this candidate
            # would fail at dispatch rather than simply going unpersonalised.
            reasons.append(f"missing {', '.join(missing)}")
        if reasons:
            excluded.append({"candidate_id": candidate.id, "name": candidate.name, "reasons": reasons})
        else:
            dialable += 1

    blockers: list[str] = []
    if agent is None:
        blockers.append("no agent has been created for this requisition yet")
    if dialable == 0:
        blockers.append("no candidate is currently dialable")

    return PreflightReport(
        candidates=len(candidates),
        dialable=dialable,
        excluded=excluded,
        agent_version_id=agent.id if agent else None,
        hunar_agent_id=agent.hunar_agent_id if agent else None,
        required_variables=list(requisition.candidate_variables),
        ready=not blockers,
        blockers=blockers,
    )


def _persist(
    session: AsyncSession,
    rows: list[Any],
    requisition_id: int,
    source: CandidateSource,
) -> list[Candidate]:
    created = []
    for row in rows:
        candidate = Candidate(
            requisition_id=requisition_id,
            name=row.name,
            phone_e164=row.phone,
            source=source,
            custom_fields=row.custom_fields,
            dedupe_key=dedupe_key(requisition_id, row.phone),
            status=(
                CandidateStatus.DO_NOT_CALL if row.status == "dnc" else CandidateStatus.NEW
            ),
        )
        session.add(candidate)
        created.append(candidate)
    return created
