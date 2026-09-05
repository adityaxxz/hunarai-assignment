"""Reconciliation, against the simulator. No network.

The simulator reproduces the two behaviours these loops exist for: it emits one
status webhook at the terminal transition and nothing in between, and its
`get_call` returns an empty result until the consistency delay elapses. So a test
that passes here would also pass against the real API for the same reasons.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.integrations.hunar.errors import HunarQuotaError, HunarServerError
from app.integrations.hunar.simulator import HunarSimulator
from app.integrations.hunar.types import AgentCreate, CallCreate, Language, VoicePersona
from app.models import (
    AgentVersion,
    Call,
    Campaign,
    CampaignKind,
    Candidate,
    CandidateSource,
)
from app.services.reconcile import (
    GIVE_UP_AFTER,
    reconcile_active_calls,
    reconcile_for_view,
    reconcile_incomplete_calls,
    reset_demand_rate_limit,
)

SCALE = 0.02


async def no_deliver(event_type: str, body: dict) -> None:
    """Webhook delivery is not what these tests are about; the point is that
    reconciliation works when nothing is delivered at all."""


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    reset_demand_rate_limit()


def agent_payload() -> AgentCreate:
    return AgentCreate(
        name="reconcile-screener",
        language=Language.ENGLISH,
        voice_persona=VoicePersona.NEHA,
        agent_prompt="Screen for {role_title}.",
        objective="Screen.",
        introduction="Hello.",
        result_prompt="Extract.",
        result_schema={"open_to_work": "boolean", "availability": "string"},
    )


async def seed_campaign(session) -> tuple[Campaign, Candidate]:
    agent = AgentVersion(
        name="screener", language="ENGLISH", voice_persona="NEHA", persona_name="N",
        agent_prompt="p", objective="o", introduction="i", result_prompt="r",
    )
    session.add(agent)
    await session.flush()
    campaign = Campaign(kind=CampaignKind.SCREENING, name="c", agent_version_id=agent.id)
    candidate = Candidate(
        name="Rider", phone_e164="+915555511111",
        source=CandidateSource.INBOUND_CSV, dedupe_key="dk-recon",
    )
    session.add_all([campaign, candidate])
    await session.flush()
    return campaign, candidate


async def place(sim: HunarSimulator, session, campaign, candidate) -> Call:
    """Dispatch one simulated call and store its id, as task 10 will."""
    agent = await sim.create_agent(agent_payload())
    created = await sim.create_call(
        CallCreate(
            agent_id=agent.id,
            callee_name=candidate.name,
            mobile_number=candidate.phone_e164,
            request_id="campaign-recon",
        )
    )
    call = Call(
        campaign_id=campaign.id, candidate_id=candidate.id, hunar_call_id=created.id
    )
    session.add(call)
    await session.commit()
    return call


async def test_active_call_advances_without_any_webhook(session) -> None:
    """The whole reason this module is the primary path: the simulator sends no
    webhook until terminal, so nothing else could move this call."""
    sim = HunarSimulator(time_scale=SCALE, deliver=no_deliver)
    campaign, candidate = await seed_campaign(session)
    call = await place(sim, session, campaign, candidate)

    assert call.status is None, "nothing has told us anything yet"

    import asyncio

    await asyncio.sleep(2 * SCALE)  # mid-dial, well before terminal
    report = await reconcile_active_calls(session, campaign.id, provider=sim)

    await session.refresh(call)
    assert report.examined == 1
    assert call.status is not None, "reconciliation is what moved the funnel"
    assert call.lifecycle_status == "IN_PROGRESS"


async def test_terminal_call_gets_its_result_on_a_later_poll(session) -> None:
    """Terminal is not complete: the API returns result {} at the moment a call
    reaches COMPLETED and fills it in minutes later.

    Also pins the backoff. The simulator runs at 0.02x but the backoff is in real
    seconds, so a poll immediately after the last one is correctly suppressed;
    the test backdates `last_reconciled_at` to stand in for the wall clock that
    would have passed in production.
    """
    import asyncio

    sim = HunarSimulator(time_scale=SCALE, deliver=no_deliver)
    campaign, candidate = await seed_campaign(session)
    for i in range(12):
        session.add(
            Candidate(
                name=f"C{i}", phone_e164="+915555511111",
                source=CandidateSource.INBOUND_CSV, dedupe_key=f"dk-poll-{i}",
            )
        )
    await session.flush()
    for i in range(12):
        await place(sim, session, campaign, candidate)

    await asyncio.sleep(8 * SCALE)  # past terminal, before the consistency reveal
    await reconcile_active_calls(session, campaign.id, provider=sim)

    engaged = (
        await session.execute(
            select(Call).where(
                Call.campaign_id == campaign.id, Call.engagement_status == "ENGAGED"
            )
        )
    ).scalars().all()
    assert engaged, "no engaged call out of 12"
    call = engaged[0]
    assert call.result is None, "result must still be empty at the moment of terminal"
    assert call.recording_url is None

    # Immediately again: the backoff should suppress every one of them.
    suppressed = await reconcile_incomplete_calls(session, campaign.id, provider=sim)
    assert suppressed.examined == 0, "backoff must stop a poll straight after the last"

    # Stand in for the backoff interval elapsing, then let the simulator reveal.
    for row in engaged:
        row.last_reconciled_at = datetime.now(timezone.utc) - timedelta(seconds=90)
    await session.commit()
    await asyncio.sleep(10 * SCALE)

    report = await reconcile_incomplete_calls(session, campaign.id, provider=sim)
    await session.refresh(call)

    assert report.updated >= 1
    assert call.result, "the trailing poll is what fetched the result"
    assert call.recording_url is not None
    # API-only fields. No webhook would ever have delivered these.
    assert call.user_speech_duration is not None


async def test_call_that_can_never_produce_a_result_is_not_polled(session) -> None:
    """NOT_CONNECTED has no result coming. Excluded at the query rather than
    polled for ten minutes."""
    sim = HunarSimulator(time_scale=SCALE, deliver=no_deliver)
    campaign, candidate = await seed_campaign(session)
    call = await place(sim, session, campaign, candidate)
    call.lifecycle_status = "NOT_CONNECTED"
    call.status = "NOT_CONNECTED"
    call.ended_at = datetime.now(timezone.utc)
    await session.commit()

    report = await reconcile_incomplete_calls(session, campaign.id, provider=sim)

    assert report.examined == 0, "should not have been selected at all"
    await session.refresh(call)
    assert call.last_reconciled_at is None


async def test_give_up_deadline_fires_and_is_recorded(session) -> None:
    sim = HunarSimulator(time_scale=SCALE, deliver=no_deliver)
    campaign, candidate = await seed_campaign(session)
    call = await place(sim, session, campaign, candidate)
    call.lifecycle_status = "COMPLETED"
    call.status = "COMPLETED"
    call.ended_at = datetime.now(timezone.utc) - GIVE_UP_AFTER - timedelta(minutes=1)
    await session.commit()

    report = await reconcile_incomplete_calls(session, campaign.id, provider=sim)

    await session.refresh(call)
    assert report.gave_up == 1
    assert report.examined == 0, "gave up instead of polling"
    assert call.reconcile_stopped_at is not None
    assert "10 minutes" in (call.reconcile_stopped_reason or "")


async def test_hunar_error_is_logged_and_counted_not_raised(session) -> None:
    """A broken Hunar must degrade to stale data, never a broken page."""

    class Broken:
        async def get_call(self, call_id: str):
            raise HunarServerError("boom", status_code=500)

    sim = HunarSimulator(time_scale=SCALE, deliver=no_deliver)
    campaign, candidate = await seed_campaign(session)
    await place(sim, session, campaign, candidate)

    report = await reconcile_active_calls(session, campaign.id, provider=Broken())

    assert report.errors == 1
    assert report.updated == 0
    assert report.quota_exhausted is False


async def test_402_stops_the_loop_and_is_surfaced_distinctly(session) -> None:
    """Out of minutes is not transient: every remaining call would fail the same
    way, so stop and say so rather than retrying quietly."""
    calls_made = []

    class OutOfMinutes:
        async def get_call(self, call_id: str):
            calls_made.append(call_id)
            raise HunarQuotaError("Subscription expired", status_code=402)

    sim = HunarSimulator(time_scale=SCALE, deliver=no_deliver)
    campaign, candidate = await seed_campaign(session)
    for i in range(3):
        candidate.dedupe_key = f"dk-quota-{i}"
        await place(sim, session, campaign, candidate)

    report = await reconcile_active_calls(session, campaign.id, provider=OutOfMinutes())

    assert report.quota_exhausted is True
    assert len(calls_made) == 1, "must stop after the first 402, not hammer the API"


async def test_demand_reconciliation_is_rate_limited(session) -> None:
    """A polling frontend across several tabs must not stampede Hunar."""
    sim = HunarSimulator(time_scale=SCALE, deliver=no_deliver)
    campaign, candidate = await seed_campaign(session)
    await place(sim, session, campaign, candidate)

    first = await reconcile_for_view(session, campaign.id, provider=sim)
    second = await reconcile_for_view(session, campaign.id, provider=sim)

    assert first.examined >= 1
    assert second.notes == ["skipped, rate limited"]
    assert second.examined == 0


async def test_internal_reconcile_requires_the_bearer_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "internal_api_token", "the-real-token")

    missing = await client.post("/internal/reconcile")
    wrong = await client.post(
        "/internal/reconcile", headers={"Authorization": "Bearer nope"}
    )
    right = await client.post(
        "/internal/reconcile", headers={"Authorization": "Bearer the-real-token"}
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert right.status_code == 200
    assert set(right.json()) == {
        "examined", "updated", "gave_up", "errors", "quota_exhausted", "notes",
    }


async def test_internal_reconcile_is_503_when_no_token_is_configured(
    client, monkeypatch
) -> None:
    """An empty configured token would make compare_digest("", "") true and open
    the endpoint to anyone."""
    monkeypatch.setattr(settings, "internal_api_token", "")

    response = await client.post(
        "/internal/reconcile", headers={"Authorization": "Bearer anything"}
    )

    assert response.status_code == 503


async def test_health_ping_needs_no_auth_and_no_database(client) -> None:
    response = await client.get("/internal/health-ping")

    assert response.status_code == 200
    assert response.json() == {"status": "awake"}
