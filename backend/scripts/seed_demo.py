"""Wipe the database and seed a coherent ninety-second demo.

Run deliberately, never on startup:

    uv run python scripts/seed_demo.py

**This deletes everything first.** That is the point — it is what makes the
script idempotent, and the alternative (upserting) would leave whatever debris a
manual walkthrough happened to create. It refuses to run unless `DEMO_MODE` is
true, so it cannot be pointed at a database that is taking real calls.

**Records are created through the real HTTP API, not by inserting rows.** The
script drives the app in-process over an ASGI transport, so requisitions go
through `RequisitionCreate` validation, agents through `agent_builder` and the
provider read-back, candidates through the CSV mapping and dedupe path, campaigns
through the same dispatch that spends money, and the one override through the
endpoint that writes `audit_log`. A seeded record is therefore structurally
identical to one a reviewer creates by clicking.

Four things are written directly, because no code path produces them on demand:

  * **Call outcomes** (`status`, `lifecycle_status`, `engagement_status`,
    `answered_by`, `result`, retry fields). These arrive from Hunar by webhook
    and reconciliation. The simulator generates them, but it randomises the
    outcome from a hash of the call id, and a demo needs one of each — a
    knock-out rejection, an undecided, a machine pickup, a pending retry.
  * **`call_events`** for one campaign, so the timeline on the candidate detail
    screen has something to show. The simulator delivers these over real HTTP to
    a running server; this script has none.
  * **`dispatch_error`** on two calls, to produce the partially-dispatched
    campaign. The simulator accepts every row it is given, so there is no way to
    make it reject one.
  * **`created_at`** backdating, so the three campaigns do not all share one
    timestamp and the list reads as a history rather than a single click.
"""

import asyncio
import io
import json
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.integrations.hunar import provider as voice_provider
from app.models import (
    AgentVersion,
    AuditLog,
    Call,
    CallEvent,
    Campaign,
    CampaignStatus,
    Candidate,
    DncEntry,
    InterviewSlot,
    Message,
    Requisition,
    SourcingSearch,
)

NOW = datetime.now(timezone.utc)

DIAL_ZONE = ZoneInfo("Asia/Kolkata")


def _in_window(days_ago: int, hour: int, minute: int = 0) -> datetime:
    """A timestamp inside the calling window the seeded campaigns are launched with.

    Anchored to the window rather than to `NOW - offset`. The seed can be run at
    any hour, and taking the clock time gave the completed batch a dial time of
    21:38 against its own 09:00-19:00 guardrail — visible the moment the
    campaign note started quoting when dialling began.
    """
    day = (NOW.astimezone(DIAL_ZONE) - timedelta(days=days_ago)).date()
    return datetime.combine(day, time(hour, minute), tzinfo=DIAL_ZONE).astimezone(
        timezone.utc
    )

# Every number here is inside the 98765-4xxxx block this repository uses for all
# synthetic data, and no name belongs to a real person. Nothing in this file may
# ever be dialled: demo mode answers with the simulator.
DNC_NUMBER = "+919876540099"


# --- the story -------------------------------------------------------------

RIDER = {
    "title": "Delivery Rider",
    "location": "Bengaluru",
    "language": "HINDI",
    "voice_persona": "NEHA",
    "shift": "Morning, 7am to 3pm",
    "pay_min": 18000,
    "pay_max": 26000,
    "openings": 40,
    "criteria": [
        {"key": "valid_licence", "question": "Do you have a valid two-wheeler driving licence?",
         "type": "boolean", "knockout": True, "weight": 0, "expected": True},
        {"key": "own_bike", "question": "Do you have your own bike?",
         "type": "boolean", "knockout": False, "weight": 3, "expected": True},
        {"key": "smartphone", "question": "Do you have a smartphone with internet?",
         "type": "boolean", "knockout": False, "weight": 2, "expected": True},
        {"key": "morning_shift", "question": "Can you work the morning shift, 7am to 3pm?",
         "type": "boolean", "knockout": False, "weight": 2, "expected": True},
    ],
    "candidate_variables": ["applied_role", "city"],
}

TECHNICIAN = {
    "title": "Lab Technician",
    "location": "Pune",
    "language": "ENGLISH",
    "voice_persona": "MIRA",
    "shift": "Rotating, includes nights",
    "pay_min": 22000,
    "pay_max": 34000,
    "openings": 6,
    "criteria": [
        {"key": "mlt_qualified", "question": "Do you have a DMLT or BSc MLT qualification?",
         "type": "boolean", "knockout": True, "weight": 0, "expected": True},
        {"key": "lab_years", "question": "How many years have you worked in a diagnostic lab?",
         "type": "string", "knockout": False, "weight": 2, "expected": None},
        {"key": "phlebotomy", "question": "Are you comfortable drawing blood samples?",
         "type": "boolean", "knockout": False, "weight": 3, "expected": True},
        {"key": "night_rotation", "question": "Can you do night shifts on rotation?",
         "type": "boolean", "knockout": False, "weight": 1, "expected": True},
    ],
    "candidate_variables": ["applied_role", "city"],
}

RIDER_CSV = """Full Name,Mobile Number,applied_role,city
Asha Kulkarni,9876540101,Delivery Rider,Bengaluru
Bilal Sheikh,9876540102,Delivery Rider,Bengaluru
Chetan Rao,9876540103,Delivery Rider,Bengaluru
Deepa Nair,9876540104,Delivery Rider,Bengaluru
Farid Ansari,9876540105,Delivery Rider,Bengaluru
Gauri Patil,9876540106,Delivery Rider,Bengaluru
Harish Kumar,9876540107,Delivery Rider,Bengaluru
Ishita Sharma,9876540099,Delivery Rider,Bengaluru
"""

TECHNICIAN_CSV = """Full Name,Mobile Number,applied_role,city
Nikhil Barve,9876540201,Lab Technician,Pune
Sanya Deshmukh,9876540202,Lab Technician,Pune
Tarun Joshi,9876540203,Lab Technician,Pune
Ujwala More,9876540204,Lab Technician,Pune
Vivek Sathe,9876540205,Lab Technician,Pune
"""

SOURCING_JD = """We are hiring a Senior Instrumentation Engineer in Pune for a process
automation project on a live plant.

The role covers loop checking, commissioning support and vendor coordination.
Four or more years of instrumentation experience is expected, ideally in oil and
gas or chemicals. This is a full-time position based at the plant.
"""

# Campaign 1, Delivery Rider. One of every outcome a reviewer needs to see, in
# the order they appear in the funnel.
RIDER_OUTCOMES: list[dict] = [
    {
        "name": "Asha Kulkarni", "stage": "engaged",
        "result": {"valid_licence": True, "own_bike": True, "smartphone": True,
                   "morning_shift": True},
        "note": "qualified, everything answered",
    },
    {
        "name": "Bilal Sheikh", "stage": "engaged",
        "result": {"valid_licence": False, "own_bike": True, "smartphone": True,
                   "morning_shift": True},
        "note": "rejected on the knock-out despite a perfect score elsewhere",
    },
    {
        "name": "Chetan Rao", "stage": "engaged",
        "result": {"own_bike": True, "smartphone": False},
        "note": "undecided: the call ended before the licence question",
    },
    {
        "name": "Deepa Nair", "stage": "exhausted",
        "result": None,
        "note": "never connected, all three attempts used",
    },
    {
        "name": "Farid Ansari", "stage": "machine",
        "result": None,
        "note": "answering machine picked up",
    },
    {
        "name": "Gauri Patil", "stage": "engaged",
        "result": {"valid_licence": False, "own_bike": True, "smartphone": True,
                   "morning_shift": True},
        "note": "computed REJECTED, recruiter overrode to QUALIFIED",
    },
    {
        "name": "Harish Kumar", "stage": "engaged",
        "result": {"valid_licence": True, "own_bike": False, "smartphone": True,
                   "morning_shift": True},
        "note": "qualified but scores lower, no bike of their own",
    },
]

# Campaign 3, Lab Technician, still in flight. Mixed mid-call states.
TECHNICIAN_INFLIGHT: dict[str, str] = {
    "Nikhil Barve": "connected",
    # The state the funnel is most often misread in: this call has not failed,
    # it is between attempts. The table says so explicitly.
    "Sanya Deshmukh": "retrying",
    "Tarun Joshi": "engaged_qualified",
    "Ujwala More": "queued",
    "Vivek Sathe": "engaged_rejected",
}

# Campaign 4, sourcing. Spread wide enough that the insights panel renders real
# distributions rather than one bar.
# How many of the fixture pool the seeded reachout consumes.
#
# **Deliberately not all of it.** The fixture provider returns a fixed set, and
# the consent gate excludes anyone who is already a candidate — so importing
# the whole pool left every later search fully disabled, the gate unrendered and
# the page ending in silence. A reviewer following the README could not complete
# the sourcing flow at all. Leaving a few behind keeps the walkthrough runnable,
# and the ones left behind include the profile with no provider number so both
# resolver badges appear among the selectable rows.
SOURCING_IMPORT_LIMIT = 8

SOURCING_RESULTS: list[dict | None] = [
    {"open_to_move": True, "notice_period": "2 months", "expected_ctc": "18 LPA",
     "preferred_callback_time": "after 7pm", "current_ctc": "14 LPA"},
    {"open_to_move": True, "notice_period": "immediate", "expected_ctc": "22 LPA",
     "preferred_callback_time": "weekday mornings"},
    {"open_to_move": True, "notice_period": "60 days", "expected_ctc": "20 LPA",
     "preferred_callback_time": "after 6pm"},
    {"open_to_move": True, "notice_period": "3 months", "expected_ctc": "26 LPA",
     "preferred_callback_time": "Saturday morning"},
    {"open_to_move": False, "reason_not_interested": "too far from home"},
    {"open_to_move": False, "reason_not_interested": "too far from home"},
    {"open_to_move": False, "reason_not_interested": "just got promoted"},
    None,  # never answered
]


# --- wiping ----------------------------------------------------------------

# Children before parents. `delete()` rather than TRUNCATE so this works on the
# in-memory SQLite the tests use as well as on Postgres.
WIPE_ORDER = (
    CallEvent, AuditLog, Call, Campaign, Message, InterviewSlot,
    Candidate, AgentVersion, SourcingSearch, Requisition, DncEntry,
)


async def wipe() -> None:
    async with SessionLocal() as session:
        for model in WIPE_ORDER:
            result = await session.execute(delete(model))
            if result.rowcount:
                print(f"  cleared {result.rowcount:>4} from {model.__tablename__}")
        await session.commit()


# --- seeding ---------------------------------------------------------------


async def seed(client: httpx.AsyncClient) -> None:
    async with SessionLocal() as session:
        session.add(
            DncEntry(phone_e164=DNC_NUMBER, reason="asked not to be contacted again")
        )
        await session.commit()

    rider_id = await _requisition(client, RIDER, RIDER_CSV)
    tech_id = await _requisition(client, TECHNICIAN, TECHNICIAN_CSV)

    finished = await _campaign(client, rider_id, "Delivery Rider — Koramangala batch")
    await _apply_rider_outcomes(finished)
    await _override(client, finished, "Gauri Patil")
    await _timeline(finished)
    await _settle(finished, CampaignStatus.COMPLETED, at=_in_window(6, 9, 55))

    partial = await _campaign(client, tech_id, "Lab Technician — first attempt")
    await _partial_failure(partial)
    await _settle(partial, CampaignStatus.PARTIALLY_DISPATCHED, at=_in_window(2, 11, 15))

    running = await _campaign(client, tech_id, "Lab Technician — second attempt")
    await _apply_technician_inflight(running)
    # Dispatched inside the window as well, so the batch is not shown dialling
    # before it was sent.
    await _settle(running, CampaignStatus.RUNNING, at=_in_window(0, 17, 30))

    sourcing = await _sourcing(client)
    await _apply_sourcing_results(sourcing)
    await _settle(sourcing, CampaignStatus.RUNNING, at=_in_window(1, 14, 5))


async def _requisition(client: httpx.AsyncClient, spec: dict, csv: str) -> int:
    created = await client.post("/requisitions", json=spec)
    created.raise_for_status()
    requisition_id = created.json()["id"]

    # Through the real agent path: builds the prompt from the criteria, pushes it
    # to the provider, and stores the variables read back rather than what we sent.
    agent = await client.post(f"/requisitions/{requisition_id}/agent")
    agent.raise_for_status()

    imported = await client.post(
        f"/requisitions/{requisition_id}/candidates/import",
        files={"file": ("candidates.csv", io.BytesIO(csv.encode()), "text/csv")},
        data={"mapping": json.dumps({
            "name": "Full Name", "phone": "Mobile Number",
            "applied_role": "applied_role", "city": "city",
        })},
    )
    imported.raise_for_status()
    summary = imported.json()
    print(
        f"  {spec['title']}: agent {agent.json()['hunar_agent_id'][:8]}…, "
        f"{summary['imported']} candidates, {summary['do_not_call']} on the do-not-call list"
    )
    return requisition_id


async def _campaign(client: httpx.AsyncClient, requisition_id: int, name: str) -> int:
    created = await client.post("/campaigns", json={
        "requisition_id": requisition_id,
        "name": name,
        "timezone": "Asia/Kolkata",
        "guardrails": {
            "allowed_days": ["MON", "TUE", "WED", "THU", "FRI"],
            "earliest_call_time": "09:00",
            "last_call_time": "19:00",
        },
        "retry_config": {"max_retry_count": 2, "retry_interval_hours": 3},
    })
    created.raise_for_status()
    body = created.json()
    print(f"  {name}: {body['total_calls']} calls dispatched")
    return body["id"]


async def _calls(session, campaign_id: int) -> dict[str, Call]:
    rows = (
        await session.execute(
            select(Call, Candidate)
            .join(Candidate, Call.candidate_id == Candidate.id)
            .where(Call.campaign_id == campaign_id)
        )
    ).all()
    return {candidate.name: call for call, candidate in rows}


def _connected(call: Call, *, seconds: float, engaged: bool, when: datetime) -> None:
    call.status = "COMPLETED"
    call.lifecycle_status = "COMPLETED"
    call.engagement_status = "ENGAGED" if engaged else "NOT_ENGAGED"
    call.answered_by = "HUMAN"
    call.duration_seconds = seconds
    call.user_speech_duration = round(seconds * 0.28, 2)
    call.started_at = when
    call.ended_at = when + timedelta(seconds=seconds)
    call.recording_url = f"https://demo-recordings.invalid/call/recording/{call.hunar_call_id}_0.wav"
    call.last_reconciled_at = call.ended_at + timedelta(minutes=10)


async def _apply_rider_outcomes(campaign_id: int) -> None:
    async with SessionLocal() as session:
        calls = await _calls(session, campaign_id)
        for spec in RIDER_OUTCOMES:
            call = calls.get(spec["name"])
            if call is None:
                continue
            if spec["stage"] == "engaged":
                _connected(call, seconds=42.0, engaged=True, when=_in_window(6, 10, 15))
                call.result = spec["result"]
            elif spec["stage"] == "exhausted":
                # Terminal, not pending. A finished campaign must not contain a
                # call that is still waiting to be dialled; the pending retry
                # lives in the running campaign instead.
                call.status = "NOT_CONNECTED"
                call.lifecycle_status = "NOT_CONNECTED"
                call.retry_count = 2
                call.retries_left = 0
                call.last_reconciled_at = _in_window(6, 10, 40)
                call.reconcile_stopped_at = _in_window(6, 10, 40)
                call.reconcile_stopped_reason = "not engaged, no result expected"
            elif spec["stage"] == "machine":
                _connected(call, seconds=11.0, engaged=False, when=_in_window(6, 10, 22))
                call.answered_by = "MACHINE"
                call.recording_url = None
                # Reconciliation gave up rather than waiting forever for a result
                # that an answering machine is never going to produce.
                call.reconcile_stopped_at = _in_window(6, 10, 35)
                call.reconcile_stopped_reason = "not engaged, no result expected"
        await session.commit()


async def _apply_technician_inflight(campaign_id: int) -> None:
    async with SessionLocal() as session:
        calls = await _calls(session, campaign_id)
        for name, stage in TECHNICIAN_INFLIGHT.items():
            call = calls.get(name)
            if call is None:
                continue
            if stage == "connected":
                call.status = "IN_PROGRESS"
                call.lifecycle_status = "IN_PROGRESS"
                call.started_at = _in_window(0, 17, 50)
            elif stage == "retrying":
                call.status = "NOT_CONNECTED"
                call.lifecycle_status = "IN_PROGRESS"
                call.retry_count = 1
                call.retries_left = 1
                # The pending retry sits inside the window as well: a next
                # attempt scheduled for 23:53 is one the guardrails forbid.
                call.next_retry_scheduled_at = _in_window(0, 18, 45)
                call.last_reconciled_at = _in_window(0, 17, 55)
            elif stage == "queued":
                call.status = "SCHEDULED"
                call.lifecycle_status = "NOT_STARTED"
            elif stage == "engaged_qualified":
                _connected(call, seconds=58.0, engaged=True, when=_in_window(0, 17, 42))
                call.result = {"mlt_qualified": True, "lab_years": "6 years",
                               "phlebotomy": True, "night_rotation": True}
            elif stage == "engaged_rejected":
                _connected(call, seconds=31.0, engaged=True, when=_in_window(0, 17, 38))
                call.result = {"mlt_qualified": False, "lab_years": "2 years",
                               "phlebotomy": True, "night_rotation": False}
        await session.commit()


async def _partial_failure(campaign_id: int) -> None:
    """Two rows Hunar refused, which is the honest-failure case.

    Written directly: the simulator accepts every row it is handed, so there is
    no way to ask it for a rejection.
    """
    async with SessionLocal() as session:
        calls = await _calls(session, campaign_id)
        for name in ("Ujwala More", "Vivek Sathe"):
            call = calls.get(name)
            if call is None:
                continue
            call.hunar_call_id = None
            call.dispatch_error = "Hunar did not accept this number in the batch"
        for name in ("Nikhil Barve", "Sanya Deshmukh", "Tarun Joshi"):
            call = calls.get(name)
            if call is not None:
                _connected(call, seconds=36.0, engaged=True, when=_in_window(2, 11, 30))
                call.result = {"mlt_qualified": True, "lab_years": "4 years",
                               "phlebotomy": True, "night_rotation": True}
        await session.commit()


async def _override(client: httpx.AsyncClient, campaign_id: int, name: str) -> None:
    """Through the real endpoint, so `audit_log` records what was overruled."""
    async with SessionLocal() as session:
        calls = await _calls(session, campaign_id)
        call_id = calls[name].id

    response = await client.post(f"/calls/{call_id}/override", json={
        "decision": "QUALIFIED",
        "reason_code": "SPOKE_TO_CANDIDATE",
        "note": "Licence was issued last week; she sent a photo of it.",
    })
    response.raise_for_status()
    print(f"  override recorded for {name}: computed REJECTED, recruiter said QUALIFIED")


async def _timeline(campaign_id: int) -> None:
    """Four webhook rows for one call, so the timeline has something to show.

    Written directly: the simulator posts these over HTTP to a running server and
    this script is not one. The timings are the ones observed in the live capture
    — the summary trails the status change by minutes, which is the whole reason
    reconciliation exists.
    """
    async with SessionLocal() as session:
        calls = await _calls(session, campaign_id)
        call = calls.get("Asha Kulkarni")
        if call is None or call.hunar_call_id is None:
            return
        base = _in_window(6, 10, 15) + timedelta(seconds=42)
        for offset, event_type in (
            (12, "call_status_updated"),
            (23, "call_recording_done"),
            (196, "call_result_done"),
            (372, "call_summary"),
        ):
            session.add(CallEvent(
                hunar_call_id=call.hunar_call_id,
                call_id=call.id,
                event_type=event_type,
                raw_body="{}",
                payload_hash=f"seed-{call.hunar_call_id}-{event_type}",
                received_at=base + timedelta(seconds=offset),
                processed_at=base + timedelta(seconds=offset, milliseconds=180),
            ))
        await session.commit()


async def _settle(campaign_id: int, status: CampaignStatus, *, at: datetime) -> None:
    """Set the final campaign status and backdate it.

    Backdating is written directly because `created_at` is a server default;
    without it all four campaigns share one timestamp and the list reads as a
    single click rather than three weeks of work.
    """
    when = at
    async with SessionLocal() as session:
        campaign = await session.get(Campaign, campaign_id)
        campaign.status = status
        campaign.created_at = when
        campaign.dispatched_at = when
        if status is CampaignStatus.PARTIALLY_DISPATCHED:
            campaign.dispatch_error = "2 of 5 numbers were not accepted"
        else:
            campaign.dispatch_error = None
        await session.commit()


async def _sourcing(client: httpx.AsyncClient) -> int:
    """The whole Module B path: JD, generated query, search, consent, dispatch."""
    search = await client.post("/sourcing/searches", json={"jd_text": SOURCING_JD})
    search.raise_for_status()
    search_body = search.json()

    # Run a fixed broad query rather than whatever the JD step produced.
    # `POST /searches` still exercises the real Gemini path and stores its output,
    # but the *seed* must be deterministic: when Gemini is reachable it writes a
    # tighter query than the keyword fallback, the fixture provider matches fewer
    # profiles, and the seeded reachout silently shrinks from eight calls to
    # three. A demo that changes shape depending on whether a third party is up
    # is not a demo.
    found = await client.post(
        f"/sourcing/searches/{search_body['id']}/run",
        json={
            "query": {"bool": {"must": [
                {"match": {"job_title": "engineer"}},
                {"term": {"location_country": "india"}},
            ]}},
            "limit": 10,
        },
    )
    found.raise_for_status()
    # Only the profiles the provider itself gave a number for, capped. What is
    # left behind is the point: see SOURCING_IMPORT_LIMIT.
    dialable = [p for p in found.json()["profiles"] if p["phone_e164"]]
    profiles = [p for p in dialable if p["resolver"] == "provider"][:SOURCING_IMPORT_LIMIT]

    imported = await client.post(
        f"/sourcing/searches/{search_body['id']}/import",
        json={"title": "Senior Instrumentation Engineer", "location": "Pune",
              "confirm": True, "profiles": profiles},
    )
    imported.raise_for_status()
    requisition_id = imported.json()["requisition_id"]

    agent = await client.post(f"/requisitions/{requisition_id}/agent")
    agent.raise_for_status()

    created = await client.post("/campaigns", json={
        "requisition_id": requisition_id,
        "name": "Instrumentation reachout — Pune",
        "kind": "SOURCING",
        "timezone": "Asia/Kolkata",
        "retry_config": {"max_retry_count": 1, "retry_interval_hours": 3},
    })
    created.raise_for_status()
    print(
        f"  sourcing: query from {search_body['query_source']}, "
        f"{imported.json()['imported']} of {len(dialable)} people imported "
        f"({len(dialable) - len(profiles)} left for a live walkthrough), "
        f"{created.json()['total_calls']} calls dispatched"
    )
    return created.json()["id"]


async def _apply_sourcing_results(campaign_id: int) -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Call).where(Call.campaign_id == campaign_id).order_by(Call.id)
            )
        ).scalars().all()
        for call, result in zip(rows, SOURCING_RESULTS, strict=False):
            if result is None:
                call.status = "NOT_CONNECTED"
                call.lifecycle_status = "NOT_CONNECTED"
                call.last_reconciled_at = _in_window(1, 14, 40)
                continue
            _connected(call, seconds=47.0, engaged=True, when=_in_window(1, 14, 20))
            call.result = result
        await session.commit()


# --- entry point -----------------------------------------------------------


async def main() -> int:
    if not settings.demo_mode:
        # The only guard that matters. This script deletes every row in eleven
        # tables; a database placing real calls must never be reachable from it.
        print("Refusing to run: DEMO_MODE is false. This script deletes all data.")
        return 1

    # A simulator whose webhook delivery goes nowhere. The default posts to
    # PUBLIC_BASE_URL over real HTTP, and there is no server listening here; the
    # call outcomes are written explicitly below instead.
    from app.integrations.hunar.simulator import HunarSimulator

    async def swallow(event_type: str, body: dict) -> None:
        return None

    voice_provider._provider = HunarSimulator(deliver=swallow)

    print("Seeding the configured database")
    print("Clearing existing data")
    await wipe()

    print("Seeding")
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://seed", timeout=60) as client:
        await seed(client)

    print("\nDone. Open /campaigns to see all four.")
    return 0


def _app():
    from app.main import app

    return app


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
