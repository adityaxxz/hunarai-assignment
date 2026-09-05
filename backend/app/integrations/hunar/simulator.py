"""In-memory Hunar stand-in, built from `fixtures/observed_shapes.md`.

Every behaviour below is copied from two real captured calls, not from the docs,
because the docs were wrong about several of them. Where the simulator is
*deliberately worse* than a naive fake would be — one status webhook instead of
five, an empty result at terminal — that is the point. A simulator that is nicer
than production lets bugs through that only appear once the key is live.

State is a dict. No database, no new dependency. It relies on running in one
process, which is exactly what Render's free tier gives us (one instance, no
workers), and is the same constraint that put `BackgroundTasks` in the webhook
receiver.
"""

import asyncio
import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.config import settings
from app.integrations.hunar.errors import HunarNotFoundError
from app.integrations.hunar.signature import compute_signature
from app.integrations.hunar.simulator_payloads import (
    DEMO_FROM_NUMBER,
    DEMO_RECORDING_HOST,
    SimCall,
    api_body,
    generate_result,
    webhook_body,
)
from app.integrations.hunar.types import (
    Agent,
    AgentCreate,
    BulkCallCreate,
    Call,
    CallCreate,
    Page,
    PhoneNumber,
)

logger = logging.getLogger(__name__)

# Compressed so a reviewer can watch a campaign finish, with the observed figure
# beside each. Offsets are seconds after the call reaches a terminal status.
STATUS_WEBHOOK_AFTER = 2.0  # observed +12s
RECORDING_WEBHOOK_AFTER = 4.0  # observed +23s
RESULT_WEBHOOK_AFTER = 5.0  # observed +27s
SUMMARY_WEBHOOK_AFTER = 12.0  # observed +372s: it waits on the maker-checker pass
# When GET /calls/{id}/ starts returning result and recording_url. Observed: the
# API returned result {} and recording_url null at the moment of COMPLETED and
# was populated some minutes later.
API_CONSISTENCY_AFTER = 5.0

# Dial progression, compressed from ~90s observed.
CONNECTED_STEPS = [("SCHEDULED", 0.0), ("INITIATED", 1.0), ("RINGING", 2.0), ("IN_PROGRESS", 3.0)]
UNANSWERED_STEPS = [("SCHEDULED", 0.0), ("RINGING", 2.0)]
TERMINAL_AFTER = 6.0
RETRY_GAP = 4.0

ENGAGED = "ENGAGED"
NOT_ENGAGED = "NOT_ENGAGED"


@dataclass
class Outcome:
    status: str
    lifecycle: str
    engagement: str | None
    answered_by: str | None
    # "the call was answered", which includes an answering machine: it still
    # reaches IN_PROGRESS and still has a duration. Only NOT_CONNECTED is False.
    connected: bool


# Observed mix is unknowable from two calls, so this is a product decision: a
# funnel that is all one colour teaches a reviewer nothing.
_OUTCOMES = [
    (0.60, Outcome("COMPLETED", "COMPLETED", ENGAGED, "HUMAN", True)),
    (0.80, Outcome("NOT_CONNECTED", "NOT_CONNECTED", None, None, False)),
    (0.90, Outcome("COMPLETED", "COMPLETED", NOT_ENGAGED, "MACHINE", True)),
    (1.00, Outcome("COMPLETED", "COMPLETED", NOT_ENGAGED, "HUMAN", True)),
]


def _rng(call_id: str, attempt: int = 0) -> random.Random:
    """Seeded from the call id so a given demo replays identically. A reviewer
    who reloads must not see a different funnel than the one being explained."""
    digest = hashlib.sha256(f"{call_id}:{attempt}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def outcome_for(call_id: str, attempt: int = 0) -> Outcome:
    roll = _rng(call_id, attempt).random()
    for threshold, outcome in _OUTCOMES:
        if roll < threshold:
            return outcome
    return _OUTCOMES[-1][1]




class HunarSimulator:
    def __init__(
        self,
        *,
        time_scale: float = 1.0,
        deliver: Any = None,
    ) -> None:
        # time_scale compresses every delay. Tests run at ~0.01; the seeded demo
        # data in task 17 uses it to build a finished campaign instantly.
        self._time_scale = time_scale
        # Injectable so tests can capture deliveries without a live server.
        self._deliver = deliver or self._http_deliver
        self._agents: dict[str, Agent] = {}
        self._calls: dict[str, SimCall] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # --- agents -----------------------------------------------------------

    async def create_agent(self, payload: AgentCreate) -> Agent:
        agent_id = str(uuid4())
        prompts = " ".join(
            [payload.agent_prompt, payload.introduction, payload.objective, payload.result_prompt]
        )
        # Observed: custom_variables came back EMPTY for an agent whose
        # introduction contained {callee_name}, because callee_name and
        # mobile_number are always required_variables. Hunar derives the custom
        # ones from the remaining {placeholder} tokens in the prompt text, so
        # that is the only way to declare one.
        required = {"callee_name", "mobile_number"}
        tokens = set(re.findall(r"\{(\w+)\}", prompts))
        agent = Agent(
            id=agent_id,
            name=payload.name,
            language=payload.language,
            voice_persona=payload.voice_persona,
            persona_name=payload.persona_name,
            agent_prompt=payload.agent_prompt,
            objective=payload.objective,
            introduction=payload.introduction,
            result_prompt=payload.result_prompt,
            result_schema=payload.result_schema,
            status="ACTIVE",
            custom_variables=sorted(tokens - required),
            required_variables=sorted(required),
            result_variables=sorted(payload.result_schema),
            created_at=datetime.now(timezone.utc),
        )
        self._agents[agent_id] = agent
        return agent

    async def get_agent(self, agent_id: str) -> Agent:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise HunarNotFoundError("Agent not found", status_code=404)
        return agent

    # --- calls ------------------------------------------------------------

    async def create_call(self, payload: CallCreate) -> Call:
        return self._start(
            agent_id=payload.agent_id,
            callee_name=payload.callee_name,
            mobile_number=payload.mobile_number,
            request_id=payload.request_id,
            timezone_name=payload.timezone or "Asia/Kolkata",
            max_retries=payload.retry_config.max_retry_count if payload.retry_config else 0,
            custom_data=payload.custom_data or {},
        )

    async def create_bulk_calls(self, payload: BulkCallCreate) -> list[Call]:
        return [
            self._start(
                agent_id=payload.agent_id,
                callee_name=row.callee_name,
                mobile_number=row.mobile_number,
                request_id=payload.request_id,
                timezone_name=payload.timezone or "Asia/Kolkata",
                max_retries=payload.retry_config.max_retry_count if payload.retry_config else 0,
                custom_data=row.custom_data or {},
            )
            for row in payload.data
        ]

    async def get_call(self, call_id: str) -> Call:
        sim = self._calls.get(call_id)
        if sim is None:
            raise HunarNotFoundError("Call not found", status_code=404)
        return Call.model_validate(api_body(sim))

    async def list_calls(
        self,
        *,
        campaign_id: str | None = None,
        agent_id: list[str] | None = None,
        status: list[str] | None = None,
        page: int = 1,
        page_size: int | None = None,
    ) -> Page[Call]:
        rows = list(self._calls.values())
        if agent_id:
            rows = [r for r in rows if r.agent_id in agent_id]
        if status:
            rows = [r for r in rows if r.status in status]
        size = page_size or 10
        start = (page - 1) * size
        window = rows[start : start + size]
        return Page[Call](
            count=len(rows),
            next=None if start + size >= len(rows) else f"?page={page + 1}",
            previous=None if page == 1 else f"?page={page - 1}",
            results=[Call.model_validate(api_body(r)) for r in window],
        )

    async def list_numbers(
        self, *, page: int = 1, page_size: int | None = None
    ) -> Page[PhoneNumber]:
        return Page[PhoneNumber](
            count=1,
            results=[
                PhoneNumber(
                    id=str(uuid4()),
                    phone_number=DEMO_FROM_NUMBER,
                    allowed_countries=["IN"],
                    country_code="IN",
                    is_default=True,
                    is_validated=True,
                    provider="SIMULATED",
                )
            ],
        )

    # --- internals --------------------------------------------------------

    def _start(
        self,
        *,
        agent_id: str,
        callee_name: str,
        mobile_number: str,
        request_id: str | None,
        timezone_name: str,
        max_retries: int,
        custom_data: dict[str, Any],
    ) -> Call:
        sim = SimCall(
            id=str(uuid4()),
            agent_id=agent_id,
            callee_name=callee_name,
            mobile_number=mobile_number,
            request_id=request_id,
            timezone=timezone_name,
            max_retries=max_retries,
            retries_left=max_retries,
            custom_data=custom_data,
        )
        self._calls[sim.id] = sim
        task = asyncio.create_task(self._run(sim))
        # Held so the task is not garbage collected mid-flight.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return Call.model_validate(api_body(sim))

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds * self._time_scale)

    async def _run(self, sim: SimCall) -> None:
        try:
            while True:
                outcome = outcome_for(sim.id, sim.retry_count)
                await self._run_attempt(sim, outcome)
                retries_left = sim.max_retries - sim.retry_count
                if outcome.status != "NOT_CONNECTED" or retries_left <= 0:
                    break
                # A retrying call is NOT a failed call: lifecycle stays
                # IN_PROGRESS while the attempt-level status rewinds. This is the
                # case the (retry_count, status_rank) ordering exists for.
                sim.lifecycle_status = "IN_PROGRESS"
                sim.retry_count += 1
                sim.retries_left = max(sim.max_retries - sim.retry_count, 0)
                sim.next_retry_scheduled_at = datetime.now(timezone.utc) + timedelta(
                    seconds=RETRY_GAP
                )
                sim.redial_status = "SCHEDULED"
                await self._sleep(RETRY_GAP)
            await self._finish(sim)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # pragma: no cover - a demo must not die silently
            logger.exception("simulated call %s failed", sim.id)

    async def _run_attempt(self, sim: SimCall, outcome: Outcome) -> None:
        steps = CONNECTED_STEPS if outcome.connected else UNANSWERED_STEPS
        previous = 0.0
        for status, at in steps:
            await self._sleep(at - previous)
            previous = at
            sim.status = status
            sim.lifecycle_status = "IN_PROGRESS"
            if status == "IN_PROGRESS":
                sim.started_at = datetime.now(timezone.utc)

        await self._sleep(TERMINAL_AFTER - previous)
        sim.status = outcome.status
        sim.lifecycle_status = outcome.lifecycle
        sim.engagement_status = outcome.engagement
        sim.answered_by = outcome.answered_by
        sim.ended_at = datetime.now(timezone.utc)
        if outcome.connected:
            rng = _rng(sim.id, sim.retry_count)
            sim.duration_seconds = round(rng.uniform(20, 90), 1)
            sim.user_speech_duration = round(sim.duration_seconds * rng.uniform(0.1, 0.4), 2)
            sim.call_ended_by = rng.choice(["AGENT", "USER"])
            sim.started_at = sim.started_at or sim.ended_at

    async def _finish(self, sim: SimCall) -> None:
        """Terminal reached. Schedule the four deliveries and the delayed API fill."""
        engaged = sim.engagement_status == ENGAGED
        if engaged:
            agent = self._agents.get(sim.agent_id)
            schema = agent.result_schema if agent and agent.result_schema else {}
            rng = _rng(sim.id, 99)
            sim.result = generate_result(schema, rng, qualified=rng.random() < 0.55)
            sim.recording_url = f"{DEMO_RECORDING_HOST}/{sim.id}_0_simulated.wav"

        # Observed on both runs: summary arrived BEFORE the status event on one
        # call and 370 seconds after it on the other. Both orders are real, so
        # the order is decided per call rather than fixed.
        summary_first = _rng(sim.id, 7).random() < 0.5
        status_at = SUMMARY_WEBHOOK_AFTER if summary_first else STATUS_WEBHOOK_AFTER
        summary_at = STATUS_WEBHOOK_AFTER if summary_first else SUMMARY_WEBHOOK_AFTER

        schedule: list[tuple[float, str]] = [
            (status_at, "call_status_updated"),
            (summary_at, "call_summary"),
        ]
        if engaged:
            schedule.append((RECORDING_WEBHOOK_AFTER, "call_recording_done"))
            schedule.append((RESULT_WEBHOOK_AFTER, "call_result_done"))

        await asyncio.gather(
            self._reveal_after(sim, API_CONSISTENCY_AFTER),
            *(self._deliver_after(sim, at, event) for at, event in schedule),
        )

    async def _reveal_after(self, sim: SimCall, delay: float) -> None:
        await self._sleep(delay)
        sim.result_visible = True

    async def _deliver_after(self, sim: SimCall, delay: float, event_type: str) -> None:
        await self._sleep(delay)
        await self._deliver(event_type, webhook_body(sim, event_type))

    async def _http_deliver(self, event_type: str, body: dict[str, Any]) -> None:
        """Delivered over real HTTP through our own receiver, on purpose.

        Demo mode therefore exercises signature verification, the idempotency
        constraint and the state machine rather than writing straight to the
        database and bypassing all three. The signature uses
        `Settings.webhook_signing_key()`, which returns the demo key in demo
        mode, so the identical verification path runs in both modes.
        """
        raw = json.dumps(body).encode("utf-8")
        url = f"{settings.public_base_url.rstrip('/')}/webhooks/hunar/{event_type}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(url, content=raw, headers=self.signed_headers(raw))
        except httpx.HTTPError as exc:
            logger.warning("simulated %s delivery failed: %s", event_type, type(exc).__name__)

    def signed_headers(self, raw: bytes) -> dict[str, str]:
        """The headers a real delivery carries. Public so a test can assert on
        the signature the simulator actually produces rather than recomputing
        one, which would pass even if this were wrong."""
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        signing_key, _ = settings.webhook_signing_key()
        # Two comma-separated segments with only the first matching, exactly as
        # observed on every real delivery.
        real = compute_signature(signing_key, timestamp, raw)
        decoy = compute_signature(f"{signing_key}-rotated", timestamp, raw)
        return {
            "Content-Type": "application/json",
            "X-Hunar-Signature": f"{real},{decoy}",
            "X-Hunar-Timestamp": timestamp,
            "User-Agent": "Hunar-Voice-Agents/1.0 (simulated)",
        }
