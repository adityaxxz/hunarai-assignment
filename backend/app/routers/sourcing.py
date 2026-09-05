"""Module B: search, resolve, consent, dispatch.

**Nothing below the surface is new.** A sourcing campaign is dispatched by the
same `POST /campaigns` as a screening one, produces the same `calls` rows, is
reconciled by the same loop and rendered by the same funnel. The only genuinely
new work is finding people, getting a number for them, and asking permission
before dialling — which is exactly the three endpoints in this file.

The last of those is the one an inbound pipeline does not need. See `confirm`.
"""

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.integrations.people_search.pdl import PeopleSearchError
from app.integrations.people_search.provider import get_people_search_provider
from app.models import (
    Call,
    Campaign,
    Candidate,
    CandidateSource,
    CandidateStatus,
    DncEntry,
    Requisition,
    RequisitionKind,
    SourcingSearch,
)
from app.schemas import (
    RESOLVER_LABELS,
    ProfileIn,
    SearchRunIn,
    SearchRunOut,
    SourcingImportIn,
    SourcingImportOut,
    SourcingInsights,
    SourcingProfileOut,
    SourcingSearchCreate,
    SourcingSearchOut,
)
from app.services.candidate_intake import dedupe_key
from app.services.contact_resolution import resolve_all
from app.services.jd_to_query import jd_to_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sourcing", tags=["sourcing"])

MAX_RESULTS = 10

# The reachout questions. Expressed as ordinary `criteria`, which is the whole
# trick: `agent_builder` turns them into the prompt, the `result_schema` and the
# rubric exactly as it does for a screening role, so Module B needs no second
# agent service, no second evaluator and no second detail screen.
SOURCING_CRITERIA: list[dict[str, Any]] = [
    {
        "key": "open_to_move",
        "question": "Are you open to hearing about a new role at the moment?",
        "type": "boolean", "knockout": True, "weight": 0, "expected": True,
    },
    {
        "key": "notice_period",
        "question": "If something did work out, how much notice would you need to serve?",
        "type": "string", "knockout": False, "weight": 1, "expected": None,
    },
    {
        "key": "expected_ctc",
        "question": "What kind of compensation would make a move worth it for you?",
        "type": "string", "knockout": False, "weight": 1, "expected": None,
    },
    {
        "key": "preferred_callback_time",
        "question": "When is a good time to call you back with details?",
        "type": "string", "knockout": False, "weight": 1, "expected": None,
    },
    {
        # Weight 0: plenty of people decline to say, and that is not a worse
        # candidate. It is captured because the recruiter needs it, not scored.
        "key": "current_ctc",
        "question": "May I ask what you are earning currently?",
        "type": "string", "knockout": False, "weight": 0, "expected": None,
    },
    {
        # Not in the five fields the brief lists, and added deliberately: without
        # it "the common objections" is an aggregate over data nobody collected.
        "key": "reason_not_interested",
        "question": "Is there something in particular keeping you where you are?",
        "type": "string", "knockout": False, "weight": 0, "expected": None,
    },
]

# Personalisation tokens. Kept to two: these are the only facts we can state
# about a stranger without sounding like we have a file on them.
SOURCING_VARIABLES = ["current_title", "current_company"]


@router.post("/searches", response_model=SourcingSearchOut, status_code=status.HTTP_201_CREATED)
async def create_search(
    payload: SourcingSearchCreate, session: AsyncSession = Depends(get_session)
) -> SourcingSearchOut:
    """JD in, query out. Runs no search and spends no credits."""
    try:
        generated = await jd_to_query(payload.jd_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    search = SourcingSearch(
        jd_text=payload.jd_text,
        generated_query=generated.query,
        provider=get_people_search_provider().name,
        result_count=0,
    )
    session.add(search)
    await session.commit()

    return SourcingSearchOut(
        id=search.id,
        jd_text=search.jd_text,
        query=generated.query,
        query_source=generated.source,
        note=generated.note,
        titles=generated.titles,
        locations=generated.locations,
        provider=search.provider,
    )


@router.post("/searches/{search_id}/run", response_model=SearchRunOut)
async def run_search(
    search_id: int, payload: SearchRunIn, session: AsyncSession = Depends(get_session)
) -> SearchRunOut:
    """Execute the (possibly edited) query, then resolve contacts.

    Writes no candidates. The recruiter has not consented to anything yet, and a
    search that quietly filled the candidates table would make the consent gate
    below decorative.
    """
    search = await _search(session, search_id)
    provider = get_people_search_provider()

    try:
        result = await provider.search(payload.query, min(payload.limit, MAX_RESULTS))
    except PeopleSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Fixture numbers stand in only when nothing real is available. On the live
    # provider that is the normal case, not a fallback: PDL's free tier returns
    # the profile and withholds the number.
    contacts = resolve_all(result.profiles, allow_fixture=True)

    existing = await _existing_phones(session)
    dnc = await _dnc_phones(session)

    search.generated_query = payload.query
    search.result_count = len(result.profiles)
    search.provider = provider.name
    await session.commit()

    profiles = [
        SourcingProfileOut(
            full_name=c.profile.full_name,
            headline=c.profile.headline,
            current_title=c.profile.current_title,
            current_company=c.profile.current_company,
            location=c.profile.location,
            linkedin_url=c.profile.linkedin_url,
            dedupe_key=c.profile.dedupe_key,
            phone_e164=c.phone_e164,
            resolver=c.resolver,
            resolver_label=RESOLVER_LABELS[c.resolver],
            resolver_detail=c.detail,
            already_a_candidate=bool(c.phone_e164 and c.phone_e164 in existing),
            do_not_call=bool(c.phone_e164 and c.phone_e164 in dnc),
        )
        for c in contacts
    ]

    return SearchRunOut(
        search_id=search.id,
        provider=result.provider,
        total_available=result.total_available,
        notes=result.notes,
        dialable=sum(1 for p in profiles if p.phone_e164),
        profiles=profiles,
    )


@router.post("/searches/{search_id}/import", response_model=SourcingImportOut)
async def import_selected(
    search_id: int, payload: SourcingImportIn, session: AsyncSession = Depends(get_session)
) -> SourcingImportOut:
    """The consent gate, and the only place sourced people become candidates.

    **An inbound pipeline does not need this and a sourcing one does, for one
    reason: these people did not apply.** A CSV of applicants is already evidence
    of interest — they gave a recruiter their number for this purpose. A profile
    scraped from a people-data vendor is not. Nobody in this list asked to be
    called, so the system refuses to turn a search result into a dialled phone
    without a person looking at the list and saying yes to it.

    Three checks, then the confirmation:
      * already a candidate — do not call the same person twice from two pipelines
      * on the do-not-call list — someone previously asked not to be contacted
      * no dialable number — nothing to call
    """
    search = await _search(session, search_id)

    if not payload.confirm:
        # Structural, not a formality. Without this the endpoint is reachable by
        # anything that can POST, and "the recruiter reviewed the list" becomes
        # an assumption rather than a recorded act.
        raise HTTPException(
            status_code=422,
            detail={
                "problems": [
                    "these people did not apply for this role, so the list has to be "
                    "confirmed before anyone is dialled. Re-send with confirm set to true."
                ]
            },
        )
    if not payload.profiles:
        raise HTTPException(status_code=422, detail={"problems": ["nothing was selected"]})

    requisition = Requisition(
        kind=RequisitionKind.SOURCING,
        title=payload.title,
        location=payload.location,
        language=payload.language,
        voice_persona=payload.voice_persona,
        criteria=SOURCING_CRITERIA,
        candidate_variables=SOURCING_VARIABLES,
    )
    session.add(requisition)
    await session.flush()

    existing = await _existing_phones(session)
    dnc = await _dnc_phones(session)

    imported: list[str] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    for profile in payload.profiles:
        reason = _skip_reason(profile, existing, dnc, seen)
        if reason:
            skipped.append({"name": profile.full_name, "reason": reason})
            continue

        phone = profile.phone_e164 or ""
        seen.add(phone)
        session.add(
            Candidate(
                requisition_id=requisition.id,
                sourcing_search_id=search.id,
                name=profile.full_name,
                phone_e164=phone,
                source=CandidateSource.SOURCED_PDL,
                status=CandidateStatus.NEW,
                dedupe_key=dedupe_key(requisition.id, phone),
                # Only the first two are declared variables and reach Hunar; the
                # rest are bookkeeping the UI reads back. `_custom_data` in the
                # campaign router filters to what the agent actually accepts.
                custom_fields={
                    "current_title": profile.current_title or "your current role",
                    "current_company": profile.current_company or "your current company",
                    "headline": profile.headline or "",
                    "linkedin_url": profile.linkedin_url or "",
                    "location": profile.location or "",
                    "contact_resolver": profile.resolver,
                },
            )
        )
        imported.append(profile.full_name)

    if not imported:
        # Nothing was written, so the requisition would be an orphan.
        await session.rollback()
        raise HTTPException(
            status_code=422,
            detail={"problems": ["every selected profile was skipped"], "skipped": skipped},
        )

    await session.commit()
    return SourcingImportOut(
        requisition_id=requisition.id,
        imported=len(imported),
        skipped=skipped,
    )


@router.get("/campaigns/{campaign_id}/insights", response_model=SourcingInsights)
async def insights(
    campaign_id: int, session: AsyncSession = Depends(get_session)
) -> SourcingInsights:
    """Aggregates over the reachout results.

    Computed from `calls.result` on read rather than stored. There are tens of
    rows, not millions, and a stored aggregate is a thing that goes stale the
    moment a late result arrives — which on Hunar's eventually-consistent API is
    routine rather than rare.
    """
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    rows = (
        await session.execute(select(Call).where(Call.campaign_id == campaign_id))
    ).scalars().all()

    answered = [c.result for c in rows if c.result]
    interested = [r for r in answered if r.get("open_to_move") is True]

    notice: dict[str, int] = {}
    for row in interested:
        value = _bucket_notice(str(row.get("notice_period") or "").strip())
        notice[value] = notice.get(value, 0) + 1

    objections: dict[str, int] = {}
    for row in answered:
        if row.get("open_to_move") is True:
            continue
        text = str(row.get("reason_not_interested") or "").strip()
        key = text or "no reason given"
        objections[key] = objections.get(key, 0) + 1

    callbacks = [
        str(r.get("preferred_callback_time")).strip()
        for r in answered
        if r.get("preferred_callback_time")
    ]

    return SourcingInsights(
        campaign_id=campaign_id,
        kind=campaign.kind,
        total_calls=len(rows),
        answered=len(answered),
        interested=len(interested),
        # Over calls that produced a result, not over everyone dialled. A rate
        # divided by people who never picked up measures reachability, not
        # interest, and the two get confused constantly.
        interest_rate=round(len(interested) / len(answered) * 100, 1) if answered else None,
        notice_period=dict(sorted(notice.items(), key=lambda kv: -kv[1])),
        objections=dict(sorted(objections.items(), key=lambda kv: -kv[1])[:8]),
        callback_times=callbacks[:20],
    )


def _skip_reason(
    profile: ProfileIn, existing: set[str], dnc: set[str], seen: set[str]
) -> str | None:
    if not profile.phone_e164:
        return "no dialable number was resolved"
    if profile.phone_e164 in dnc:
        return "on the do-not-call list"
    if profile.phone_e164 in existing:
        return "already a candidate from an earlier import"
    if profile.phone_e164 in seen:
        return "the same number appears twice in this selection"
    return None


_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "six": 6}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}


def _bucket_notice(value: str) -> str:
    """Free text into a handful of buckets.

    The agent records what the person said, and "2 months", "60 days" and "two
    months" are one fact reported three ways. Parsed as a duration rather than
    matched as substrings — the first version of this looked for "0 " and put
    "60 days" in the immediate bucket.
    """
    if not value:
        return "not stated"
    lowered = value.lower()
    if any(w in lowered for w in ("immediate", "right away", "asap", "no notice")):
        return "immediate"

    match = re.search(r"(\d+|" + "|".join(_WORD_NUMBERS) + r")\s*(day|week|month)", lowered)
    if not match:
        return value[:40]

    count = _WORD_NUMBERS.get(match.group(1), 0) or int(
        match.group(1) if match.group(1).isdigit() else 0
    )
    days = count * _UNIT_DAYS[match.group(2)]
    if days <= 7:
        return "immediate"
    if days <= 35:
        return "1 month"
    if days <= 65:
        return "2 months"
    if days <= 95:
        return "3 months"
    return "more than 3 months"


async def _search(session: AsyncSession, search_id: int) -> SourcingSearch:
    search = await session.get(SourcingSearch, search_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    return search


async def _existing_phones(session: AsyncSession) -> set[str]:
    # Across every requisition, not just this one. The point of the check is that
    # the same person is not called by two pipelines in the same week.
    return set((await session.execute(select(Candidate.phone_e164))).scalars())


async def _dnc_phones(session: AsyncSession) -> set[str]:
    return set((await session.execute(select(DncEntry.phone_e164))).scalars())
