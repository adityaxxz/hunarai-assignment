"""Requisitions, and pushing their generated agent config to Hunar."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.integrations.hunar.errors import HunarError, HunarQuotaError, HunarValidationError
from app.integrations.hunar.provider import get_voice_provider
from app.integrations.hunar.types import AgentCreate
from app.models import AgentVersion, Requisition
from app.schemas import (
    AgentVersionRead,
    RequisitionCreate,
    RequisitionRead,
    RequisitionUpdate,
)
from app.services.agent_builder import AgentBuildError, build_agent_payload, validate_agent_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/requisitions", tags=["requisitions"])


async def _get(session: AsyncSession, requisition_id: int) -> Requisition:
    requisition = await session.get(Requisition, requisition_id)
    if requisition is None:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return requisition


def _to_model(requisition: Requisition) -> RequisitionRead:
    return RequisitionRead.model_validate(requisition, from_attributes=True)


@router.post("", response_model=RequisitionRead, status_code=status.HTTP_201_CREATED)
async def create_requisition(
    payload: RequisitionCreate, session: AsyncSession = Depends(get_session)
) -> RequisitionRead:
    requisition = Requisition(
        **payload.model_dump(exclude={"criteria"}),
        criteria=[c.model_dump() for c in payload.criteria],
    )
    session.add(requisition)
    await session.commit()
    return _to_model(requisition)


@router.get("", response_model=list[RequisitionRead])
async def list_requisitions(
    session: AsyncSession = Depends(get_session),
) -> list[RequisitionRead]:
    rows = (await session.execute(select(Requisition).order_by(Requisition.id))).scalars()
    return [_to_model(r) for r in rows]


@router.get("/{requisition_id}", response_model=RequisitionRead)
async def get_requisition(
    requisition_id: int, session: AsyncSession = Depends(get_session)
) -> RequisitionRead:
    return _to_model(await _get(session, requisition_id))


@router.put("/{requisition_id}", response_model=RequisitionRead)
async def update_requisition(
    requisition_id: int,
    payload: RequisitionUpdate,
    session: AsyncSession = Depends(get_session),
) -> RequisitionRead:
    requisition = await _get(session, requisition_id)
    for field, value in payload.model_dump(exclude={"criteria"}).items():
        setattr(requisition, field, value)
    requisition.criteria = [c.model_dump() for c in payload.criteria]
    await session.commit()
    return _to_model(requisition)


@router.delete("/{requisition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_requisition(
    requisition_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    await session.delete(await _get(session, requisition_id))
    await session.commit()


@router.post("/{requisition_id}/agent/preview", response_model=AgentCreate)
async def preview_agent(
    requisition_id: int, session: AsyncSession = Depends(get_session)
) -> AgentCreate:
    """The exact payload that would be sent, without sending it.

    A real dry run, not a formatted summary: the recruiter edits this and what
    they see is what Hunar receives. This screen is the one that best shows what
    the job actually is, so it must not be an approximation of the request.
    """
    requisition = await _get(session, requisition_id)
    payload = build_agent_payload(requisition)
    _validate(requisition, payload)
    return payload


@router.post(
    "/{requisition_id}/agent",
    response_model=AgentVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    requisition_id: int,
    payload: AgentCreate | None = None,
    session: AsyncSession = Depends(get_session),
) -> AgentVersionRead:
    """Push the agent to Hunar and record what came back.

    `payload` is optional: omit it to send the generated config as-is, or supply
    the edited version from the preview panel. What is stored is what was sent.
    """
    requisition = await _get(session, requisition_id)
    payload = payload or build_agent_payload(requisition)
    _validate(requisition, payload)

    provider = get_voice_provider()
    try:
        created = await provider.create_agent(payload)
        # Read back rather than trusting the create response or, worse, what we
        # sent. Hunar DERIVES custom_variables from {token}s in the prompt, and
        # the live capture returned an empty list for an agent we assumed would
        # have one. The only reliable statement of the contract is Hunar's own.
        agent = await provider.get_agent(created.id)
    except HunarQuotaError as exc:
        raise HTTPException(status_code=402, detail=f"Hunar account cannot be used: {exc.message}") from exc
    except HunarValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": exc.message, "fields": exc.field_errors},
        ) from exc
    except HunarError as exc:
        logger.warning("agent creation failed for requisition %s: %s", requisition_id, exc)
        raise HTTPException(status_code=502, detail=f"Hunar rejected the agent: {exc}") from exc

    next_version = (
        await session.execute(
            select(func.coalesce(func.max(AgentVersion.version), 0)).where(
                AgentVersion.requisition_id == requisition_id
            )
        )
    ).scalar_one() + 1

    # A new row every time, never an update. A prompt change that produced a
    # different agent id has to stay traceable to the calls made under it.
    version = AgentVersion(
        requisition_id=requisition_id,
        version=next_version,
        name=payload.name,
        language=payload.language,
        voice_persona=payload.voice_persona,
        persona_name=payload.persona_name or "",
        agent_prompt=payload.agent_prompt,
        objective=payload.objective,
        introduction=payload.introduction,
        result_prompt=payload.result_prompt,
        result_schema=payload.result_schema,
        hunar_agent_id=agent.id,
        custom_variables=agent.custom_variables,
        required_variables=agent.required_variables,
        result_variables=agent.result_variables,
    )
    session.add(version)
    await session.commit()
    return AgentVersionRead.model_validate(version, from_attributes=True)


@router.get("/{requisition_id}/agent", response_model=list[AgentVersionRead])
async def list_agent_versions(
    requisition_id: int, session: AsyncSession = Depends(get_session)
) -> list[AgentVersionRead]:
    await _get(session, requisition_id)
    rows = (
        await session.execute(
            select(AgentVersion)
            .where(AgentVersion.requisition_id == requisition_id)
            .order_by(AgentVersion.version)
        )
    ).scalars()
    return [AgentVersionRead.model_validate(r, from_attributes=True) for r in rows]


def _validate(requisition: Requisition, payload: AgentCreate) -> None:
    try:
        validate_agent_payload(requisition, payload)
    except AgentBuildError as exc:
        # 422 with the specific missing item, never a 500. The recruiter can act
        # on "city has no {token} in the prompt"; they cannot act on a traceback.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
