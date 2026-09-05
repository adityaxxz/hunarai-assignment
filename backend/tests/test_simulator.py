"""Simulator fidelity.

The assertions here are mostly that the simulator is *not better than
production*: one status webhook rather than one per transition, an empty result
at the moment of terminal. A friendlier fake would hide exactly the bugs that
only surface once the real key is live.

Every simulated call runs at time_scale 0.01, so the 12-second summary delay is
120ms.
"""

import asyncio
import json

import pytest

from app.config import settings
from app.integrations.hunar.provider import VoiceProvider
from app.integrations.hunar.signature import verify_signature
from app.integrations.hunar.simulator import HunarSimulator, outcome_for
from app.integrations.hunar.types import (
    AgentCreate,
    BulkCallCreate,
    BulkCallRecipient,
    CallCreate,
    Language,
    RetryConfig,
    VoicePersona,
)
from sqlalchemy import select

from app.models import Call as CallRow
from app.models import CallEvent
from app.services.call_event_parser import parse_event
from app.services.call_state import apply_call_update

SCALE = 0.01


def make_agent_payload() -> AgentCreate:
    return AgentCreate(
        name="demo-screener",
        language=Language.ENGLISH,
        voice_persona=VoicePersona.NEHA,
        agent_prompt="Screen the candidate for {role_title}.",
        objective="Screen.",
        introduction="Hello {callee_name}.",
        result_prompt="Extract the answers.",
        result_schema={"open_to_work": "boolean", "availability": "string"},
    )


class Collector:
    """Captures deliveries instead of POSTing them, but keeps the real signing so
    the signature can still be checked."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, event_type: str, body: dict) -> None:
        self.events.append((event_type, body))


async def run_call(sim: HunarSimulator, *, retries: int = 0) -> tuple[str, str]:
    """Place one call and wait for the whole simulated timeline. Returns
    (call_id, agent_id)."""
    agent = await sim.create_agent(make_agent_payload())
    call = await sim.create_call(
        CallCreate(
            agent_id=agent.id,
            callee_name="Demo Candidate",
            mobile_number="+915555511111",
            request_id="campaign-1",
            retry_config=RetryConfig(max_retry_count=retries, retry_interval_hours=0)
            if retries
            else None,
        )
    )
    # Long enough to cover progression + the summary at +12s, scaled.
    await asyncio.sleep(40 * SCALE)
    return call.id, agent.id


async def find_call_with(sim_factory, predicate, limit: int = 40) -> tuple:
    """Outcomes are deterministic per call id, so to test a specific outcome we
    place calls until one lands on it rather than forcing internal state."""
    for _ in range(limit):
        collector = Collector()
        sim = sim_factory(collector)
        call_id, agent_id = await run_call(sim, retries=2)
        call = await sim.get_call(call_id)
        if predicate(call, collector):
            return sim, collector, call_id, agent_id
    raise AssertionError("no simulated call matched the predicate")


def make_sim(collector: Collector) -> HunarSimulator:
    return HunarSimulator(time_scale=SCALE, deliver=collector)


async def test_simulator_satisfies_the_voice_provider_protocol() -> None:
    assert isinstance(HunarSimulator(), VoiceProvider)


async def test_factory_switches_on_demo_mode(monkeypatch) -> None:
    """The whole point of the seam: the deployed demo keeps working after the
    trial key is revoked, because nothing above this line knows which one it got."""
    from app.integrations.hunar.client import HunarClient
    from app.integrations.hunar.provider import get_voice_provider, reset_voice_provider

    monkeypatch.setattr(settings, "demo_mode", True)
    reset_voice_provider()
    assert isinstance(get_voice_provider(), HunarSimulator)
    # Same instance on the next call: the simulator's entire state is in memory,
    # so a fresh object would lose every call it had placed.
    assert get_voice_provider() is get_voice_provider()

    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "hunar_api_key", "a-key")
    reset_voice_provider()
    assert isinstance(get_voice_provider(), HunarClient)
    reset_voice_provider()


async def test_connected_call_delivers_four_events_with_exactly_one_status() -> None:
    """Observed: five status transitions produced ONE call_status_updated, at the
    terminal transition. The simulator must not deliver a nicer stream than that,
    because that is what forces reconciliation to be the funnel's data source."""
    sim, collector, call_id, _ = await find_call_with(
        make_sim, lambda call, c: call.engagement_status == "ENGAGED"
    )

    delivered = [event for event, _ in collector.events]

    assert sorted(delivered) == [
        "call_recording_done",
        "call_result_done",
        "call_status_updated",
        "call_summary",
    ]
    assert delivered.count("call_status_updated") == 1


async def test_result_is_empty_at_terminal_and_populated_after_the_delay() -> None:
    """The API was observed returning result {} and recording_url null at the
    moment of COMPLETED, filling in minutes later. This is what exercises the
    re-poll-terminal-calls loop in task 7.

    Runs at a coarser scale than the other tests: the gap between terminal (+6)
    and the consistency reveal (+11) is only five units, and Windows' timer
    granularity is too lumpy to land inside it at scale 0.01.
    """
    scale = 0.05
    sim = HunarSimulator(time_scale=scale, deliver=Collector())
    agent = await sim.create_agent(make_agent_payload())
    created = await sim.create_bulk_calls(
        BulkCallCreate(
            agent_id=agent.id,
            data=[
                BulkCallRecipient(callee_name=f"C{i}", mobile_number="+915555511111")
                for i in range(20)
            ],
        )
    )

    # Terminal is at +6, the reveal at +11. Look at +8.
    await asyncio.sleep(8 * scale)
    at_terminal = [await sim.get_call(c.id) for c in created]
    engaged = [c for c in at_terminal if c.engagement_status == "ENGAGED"]
    assert engaged, "no engaged call produced out of 20"
    for call in engaged:
        assert call.lifecycle_status == "COMPLETED"
        assert call.result == {}, "result must be empty at the moment of terminal"
        assert call.recording_url is None

    await asyncio.sleep(10 * scale)
    for call in engaged:
        later = await sim.get_call(call.id)
        assert later.result, "result must be populated after the consistency delay"
        assert later.recording_url is not None
        assert set(later.result) == {"open_to_work", "availability"}
        assert isinstance(later.result["open_to_work"], bool)


async def test_simulated_webhooks_pass_the_real_signature_check(monkeypatch) -> None:
    """Demo mode must exercise verification, not bypass it.

    Asserts on the header the simulator itself produces via `signed_headers`.
    Recomputing the signature in the test would pass even if the simulator signed
    with the wrong key.
    """
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "demo_webhook_signing_key", "a-demo-signing-key")

    sim = HunarSimulator(time_scale=SCALE, deliver=Collector())
    raw = json.dumps({"call_id": "c-1", "event_type": "call_summary"}).encode()
    headers = sim.signed_headers(raw)

    key, env_var = settings.webhook_signing_key()
    assert env_var == "DEMO_WEBHOOK_SIGNING_KEY", "demo mode must not use the API key"
    header = headers["X-Hunar-Signature"]
    assert len(header.split(",")) == 2, "two segments, as observed on every delivery"
    assert verify_signature(key, headers["X-Hunar-Timestamp"], raw, header)
    assert not verify_signature("wrong-key", headers["X-Hunar-Timestamp"], raw, header)
    # Only the first segment is valid, matching what Hunar actually sends.
    first, second = header.split(",")
    assert verify_signature(key, headers["X-Hunar-Timestamp"], raw, first)
    assert not verify_signature(key, headers["X-Hunar-Timestamp"], raw, second)


async def test_outcome_is_deterministic_for_a_given_call_id() -> None:
    """A reviewer who reloads must not see a different funnel."""
    for call_id in ("abc-1", "abc-2", "9f2c-dead-beef"):
        first, second = outcome_for(call_id), outcome_for(call_id)
        assert (first.status, first.engagement, first.answered_by) == (
            second.status,
            second.engagement,
            second.answered_by,
        )
    # And different ids do not all collapse to one outcome.
    assert len({outcome_for(f"c{i}").status for i in range(50)}) > 1


async def test_retry_rewinds_status_and_the_state_machine_applies_it() -> None:
    """The whole reason the ordering key is (retry_count, status_rank): attempt 0
    ends NOT_CONNECTED, then the retry goes back to SCHEDULED and RINGING."""
    sim, collector, call_id, _ = await find_call_with(
        make_sim, lambda call, c: call.retry_count > 0
    )
    call = await sim.get_call(call_id)

    assert call.retry_count > 0

    # Replay: terminal on attempt 0, then a rewound status on attempt 1.
    row = CallRow(campaign_id=1, candidate_id=1, hunar_call_id=call_id)
    from app.services.call_state import CallUpdate

    apply_call_update(row, CallUpdate(call_id, status="NOT_CONNECTED", retry_count=0))
    assert row.status == "NOT_CONNECTED"

    changed = apply_call_update(row, CallUpdate(call_id, status="RINGING", retry_count=1))
    assert changed is True, "a retry update must not be discarded as stale"
    assert row.status == "RINGING"
    assert row.retry_count == 1


async def test_webhook_bodies_use_call_id_and_omit_api_only_fields() -> None:
    """Webhook shape is not call-detail shape, and parse_event must read it."""
    sim, collector, call_id, _ = await find_call_with(
        make_sim, lambda call, c: call.engagement_status == "ENGAGED"
    )

    for event_type, body in collector.events:
        assert "call_id" in body and "id" not in body
        assert "engagement_status" not in body
        assert "user_speech_duration" not in body
        if event_type in ("call_status_updated", "call_summary"):
            assert "to_number" in body and "mobile_number" not in body
        update = parse_event(event_type, json.dumps(body))
        assert update is not None and update.hunar_call_id == call_id


async def test_demo_deliveries_traverse_the_real_receiver_end_to_end(
    client, session, monkeypatch
) -> None:
    """The claim demo mode rests on: simulated webhooks go over HTTP through our
    own endpoint, so signature verification, the idempotency constraint and the
    state machine all run exactly as they do in live mode.

    The simulator's httpx client is pointed at the ASGI app instead of a socket;
    everything else is the production path.
    """
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "demo_webhook_signing_key", "a-demo-signing-key")

    sim = HunarSimulator(time_scale=SCALE)
    agent = await sim.create_agent(make_agent_payload())

    delivered: list[tuple[str, int]] = []
    # Serialised only because the whole suite shares ONE SQLite connection via
    # StaticPool, so concurrent sessions interleave transactions on it and lose
    # writes. Production runs Postgres with a real pool and does not need this.
    lock = asyncio.Lock()

    async def deliver_through_the_app(event_type: str, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        async with lock:
            response = await client.post(
                f"/webhooks/hunar/{event_type}",
                content=raw,
                headers=sim.signed_headers(raw),
            )
        delivered.append((event_type, response.status_code))

    sim._deliver = deliver_through_the_app  # noqa: SLF001 - the injection seam

    created = await sim.create_bulk_calls(
        BulkCallCreate(
            agent_id=agent.id,
            request_id="campaign-demo",
            data=[
                BulkCallRecipient(callee_name=f"C{i}", mobile_number="+915555511111")
                for i in range(6)
            ],
        )
    )
    await asyncio.sleep(40 * SCALE)

    assert delivered, "no webhooks were delivered"
    assert {code for _, code in delivered} == {200}, "every delivery must be accepted"

    stored = (await session.execute(select(CallEvent))).scalars().all()
    assert len(stored) == len(delivered)
    assert {e.hunar_call_id for e in stored} <= {c.id for c in created}
    # The receiver stored raw bytes and pulled the id out of the webhook shape.
    assert all(e.hunar_call_id is not None for e in stored)
    assert all(json.loads(e.raw_body)["event_type"] == e.event_type for e in stored)

    # Redelivery is rejected by the unique constraint, not by a check-then-insert.
    event_type, body = "call_summary", json.loads(stored[0].raw_body)
    raw = json.dumps(body).encode("utf-8")
    first = await client.post(f"/webhooks/hunar/{event_type}", content=raw, headers=sim.signed_headers(raw))
    second = await client.post(f"/webhooks/hunar/{event_type}", content=raw, headers=sim.signed_headers(raw))
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["status"] == "duplicate"
