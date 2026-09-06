"""Polling Hunar for call state. The primary funnel data path, not a backstop.

Three things measured from real calls (`fixtures/observed_shapes.md`) make this
the main path rather than a repair mechanism:

1. **`call_status_updated` fires once, at the terminal transition.** A call that
   moved SCHEDULED -> INITIATED -> RINGING -> IN_PROGRESS -> COMPLETED produced
   exactly one webhook, with the catcher running throughout. Every intermediate
   funnel state is therefore invisible to the webhook stream and only reachable
   by polling.
2. **Terminal is not complete.** At the moment a call reached COMPLETED,
   `GET /calls/{id}/` returned `result: {}` and `recording_url: null`. Both were
   populated minutes later. So a call finishing is the start of the wait, not the
   end of it.
3. **`engagement_status`, `call_ended_by`, `redial_status` and
   `user_speech_duration` never appear in any webhook.** Confirmed on a
   connected, engaged call. They exist only on the API response, so any funnel
   stage keyed on engagement depends on this module having run.

Two loops, kept separate because they stop for different reasons: one runs until
a call goes terminal, the other until the trailing fields arrive or we give up.

Nothing here raises into a request. A Hunar 5xx, a timeout or a 402 degrades to
stale data plus a logged error and a count in the report.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.hunar.errors import HunarError, HunarQuotaError
from app.integrations.hunar.provider import VoiceProvider, get_voice_provider
from app.integrations.hunar.types import Call as ApiCall
from app.models import Call, Campaign
from app.services.call_state import CallUpdate, apply_call_update

logger = logging.getLogger(__name__)

TERMINAL_LIFECYCLE = {"COMPLETED", "NOT_CONNECTED", "FAILED", "CANCELLED"}

# A call that never connected, failed or was cancelled will never produce a
# result or a recording. Polling those until timeout is ten minutes of requests
# for an answer that does not exist.
CANNOT_PRODUCE_RESULT = {"NOT_CONNECTED", "FAILED", "CANCELLED"}

# Give up this long after ended_at. The observed gap between COMPLETED and the
# result appearing was a few minutes; ten is generous without being unbounded.
GIVE_UP_AFTER = timedelta(minutes=10)

# Backoff for the trailing-fields poll, by how long ago the call ended. Fast
# while the result is plausibly imminent, slow once it is probably not coming.
_BACKOFF = (
    (timedelta(minutes=1), timedelta(seconds=15)),
    (timedelta(minutes=3), timedelta(seconds=30)),
    (GIVE_UP_AFTER, timedelta(seconds=60)),
)

# Bounded so a sweep never walks the whole org: the account we are on already has
# 600+ calls from other people's testing.
BACKSTOP_PAGE_SIZE = 200
BACKSTOP_MAX_PAGES = 3


@dataclass
class ReconcileReport:
    examined: int = 0
    updated: int = 0
    gave_up: int = 0
    errors: int = 0
    # Surfaced separately because it is not a transient failure: a 402 means the
    # account is out of calling minutes and every subsequent call will fail too.
    # Worth showing a user rather than retrying quietly forever.
    quota_exhausted: bool = False
    notes: list[str] = field(default_factory=list)

    def merge(self, other: "ReconcileReport") -> "ReconcileReport":
        return ReconcileReport(
            examined=self.examined + other.examined,
            updated=self.updated + other.updated,
            gave_up=self.gave_up + other.gave_up,
            errors=self.errors + other.errors,
            quota_exhausted=self.quota_exhausted or other.quota_exhausted,
            notes=self.notes + other.notes,
        )


def update_from_api(call: ApiCall) -> CallUpdate:
    """The API response's fields, mapped to the same `CallUpdate` a webhook
    produces, so both paths converge on one `apply_call_update`.

    This is where the four API-only fields enter the system. `call_event_parser`
    deliberately does not read them, because no webhook carries them.
    """
    return CallUpdate(
        hunar_call_id=call.id,
        status=call.status,
        lifecycle_status=call.lifecycle_status,
        engagement_status=call.engagement_status,
        answered_by=call.answered_by,
        call_ended_by=call.call_ended_by,
        redial_status=call.redial_status,
        retry_count=call.retry_count,
        retries_left=call.retries_left,
        next_retry_scheduled_at=call.next_retry_scheduled_at,
        recording_url=call.recording_url,
        # `{}` is normalised away here so an empty result never reaches the
        # record. See apply_call_update: the observed sequence is a real result
        # arriving by webhook, then a poll returning {}.
        result=call.result or None,
        duration_seconds=call.duration_seconds,
        user_speech_duration=call.user_speech_duration,
        started_at=call.started_at,
        ended_at=call.ended_at,
    )


async def reconcile_active_calls(
    session: AsyncSession,
    campaign_id: int | None = None,
    provider: VoiceProvider | None = None,
) -> ReconcileReport:
    """Re-read every call that has not reached a terminal lifecycle.

    This is what moves the funnel. Without it a call sits at whatever the last
    webhook said, which for an in-flight call is nothing at all.
    """
    provider = provider or get_voice_provider()
    report = ReconcileReport()

    stmt = select(Call).where(
        Call.hunar_call_id.is_not(None),
        or_(
            Call.lifecycle_status.is_(None),
            Call.lifecycle_status.not_in(TERMINAL_LIFECYCLE),
        ),
    )
    if campaign_id is not None:
        stmt = stmt.where(Call.campaign_id == campaign_id)

    calls = (await session.execute(stmt)).scalars().all()
    for call in calls:
        report.examined += 1
        if not await _refresh(session, call, provider, report):
            break

    await session.commit()
    return report


async def reconcile_incomplete_calls(
    session: AsyncSession,
    campaign_id: int | None = None,
    provider: VoiceProvider | None = None,
) -> ReconcileReport:
    """Chase the fields that arrive after a call is already terminal.

    Different stop condition from the active loop: these calls are finished, and
    we are waiting on the maker-checker result and the recording upload.
    """
    provider = provider or get_voice_provider()
    report = ReconcileReport()
    now = datetime.now(timezone.utc)

    stmt = select(Call).where(
        Call.hunar_call_id.is_not(None),
        Call.lifecycle_status.in_(TERMINAL_LIFECYCLE),
        Call.reconcile_stopped_at.is_(None),
        # engagement_status belongs here as much as the other two: it is
        # API-only, it never arrives on a webhook, and reconciliation is the only
        # thing that fetches it. Leaving it out meant a call whose result and
        # recording both arrived by webhook was never re-read, so engagement
        # stayed null forever and the funnel showed Completed where it should
        # have shown Engaged. Observed on the first real screening call.
        or_(
            Call.result.is_(None),
            Call.recording_url.is_(None),
            Call.engagement_status.is_(None),
        ),
        # Outcomes that cannot produce a result are excluded at the query rather
        # than polled until they time out.
        Call.lifecycle_status.not_in(CANNOT_PRODUCE_RESULT),
    )
    if campaign_id is not None:
        stmt = stmt.where(Call.campaign_id == campaign_id)

    for call in (await session.execute(stmt)).scalars().all():
        # An answered call that never engaged has nothing to extract either, but
        # engagement_status is only known once we have polled at least once.
        if call.engagement_status == "NOT_ENGAGED":
            _stop(call, now, "not engaged, no result expected")
            report.gave_up += 1
            continue

        if _past_deadline(call, now):
            _stop(call, now, _still_missing(call))
            report.gave_up += 1
            continue

        if not _due_for_poll(call, now):
            continue

        report.examined += 1
        if not await _refresh(session, call, provider, report):
            break

    await session.commit()
    return report


async def reconcile_campaign_backstop(
    session: AsyncSession,
    campaign_id: int,
    provider: VoiceProvider | None = None,
) -> ReconcileReport:
    """Page Hunar's call list to find calls we never recorded an id for.

    We cannot ask Hunar for "the calls in campaign X". Undocumented query params
    are **silently ignored rather than rejected** — probed against the live API,
    `request_id`, `lifecycle_status` and `created_after` each returned the full
    unfiltered set of 615 calls, while the documented `status` filter correctly
    narrowed it to 287. A filter that matched nothing looks exactly like a filter
    that matched everything, which is the dangerous kind of wrong.

    So the primary path is by id (we store `hunar_call_id` at dispatch) and this
    is the backstop for the window where Hunar accepted a call but our write
    failed: page by the documented `agent_id` filter, match `request_id`
    client-side, and bind anything unrecognised to a call row that is still
    waiting for an id.
    """
    provider = provider or get_voice_provider()
    report = ReconcileReport()

    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or not campaign.request_id:
        return report

    hunar_agent_id = await _campaign_agent_id(session, campaign)
    if not hunar_agent_id:
        return report

    known = {
        row
        for row in (
            await session.execute(
                select(Call.hunar_call_id).where(Call.campaign_id == campaign_id)
            )
        ).scalars()
        if row
    }

    for page in range(1, BACKSTOP_MAX_PAGES + 1):
        try:
            listing = await provider.list_calls(
                agent_id=[hunar_agent_id], page=page, page_size=BACKSTOP_PAGE_SIZE
            )
        except HunarQuotaError as exc:
            report.quota_exhausted = True
            report.errors += 1
            logger.warning("reconcile backstop stopped: %s", exc)
            return report
        except HunarError as exc:
            report.errors += 1
            logger.warning("reconcile backstop page %d failed: %s", page, exc)
            return report

        for api_call in listing.results:
            if api_call.request_id != campaign.request_id or api_call.id in known:
                continue
            report.examined += 1
            if await _adopt(session, campaign_id, api_call):
                report.updated += 1
            else:
                report.notes.append(f"unmatched hunar call {api_call.id[:8]}")

        if not listing.next:
            break

    await session.commit()
    return report


async def _campaign_agent_id(session: AsyncSession, campaign: Campaign) -> str | None:
    from app.models import AgentVersion

    agent = await session.get(AgentVersion, campaign.agent_version_id)
    return agent.hunar_agent_id if agent else None


async def _adopt(session: AsyncSession, campaign_id: int, api_call: ApiCall) -> bool:
    """Bind a Hunar call we did not know about to the row for that phone number.

    Matched on `mobile_number`, not on "the first row still missing an id". The
    ordering heuristic this replaces was wrong under any concurrency: two rows
    awaiting ids meant a coin flip, and losing it attributes one candidate's
    screening result — their licence, their availability, their rejection — to a
    different person. That is the worst class of bug this system can have, and it
    would look like working software.

    Phone number is a safe key within a campaign because Hunar's bulk endpoint
    defaults `remove_duplicate_phone_numbers` to true, so a batch cannot contain
    the same number twice, and our own import dedupes on the normalised number
    before that.
    """
    from app.models import Candidate

    if not api_call.mobile_number:
        return False

    row = (
        await session.execute(
            select(Call)
            .join(Candidate, Call.candidate_id == Candidate.id)
            .where(
                Call.campaign_id == campaign_id,
                Call.hunar_call_id.is_(None),
                Candidate.phone_e164 == api_call.mobile_number,
            )
        )
    ).scalars().first()
    if row is None:
        return False
    row.hunar_call_id = api_call.id
    apply_call_update(row, update_from_api(api_call))
    return True


async def _refresh(
    session: AsyncSession,
    call: Call,
    provider: VoiceProvider,
    report: ReconcileReport,
) -> bool:
    """Re-read one call. Returns False when the caller should stop entirely.

    Only a 402 stops the loop: it means the account is out of minutes, so every
    remaining request would fail the same way and hammering the API achieves
    nothing. Anything else is counted and skipped.
    """
    try:
        api_call = await provider.get_call(call.hunar_call_id or "")
    except HunarQuotaError as exc:
        report.quota_exhausted = True
        report.errors += 1
        logger.warning("reconciliation stopped, account out of minutes: %s", exc)
        return False
    except HunarError as exc:
        report.errors += 1
        logger.warning("could not reconcile call %s: %s", call.id, exc)
        return True
    except Exception:  # pragma: no cover - never break a page over this
        report.errors += 1
        logger.exception("unexpected failure reconciling call %s", call.id)
        return True

    # Exactly one place mutates a call, whether the data arrived by webhook or by
    # poll. Duplicating the ordering rules here is how the two paths drift.
    if apply_call_update(call, update_from_api(api_call)):
        report.updated += 1
    call.last_reconciled_at = datetime.now(timezone.utc)
    return True


def _as_utc(value: datetime) -> datetime:
    """Treat a naive timestamp as UTC.

    SQLite has no timezone type and hands back naive datetimes, while Postgres
    returns aware ones, so arithmetic against `now` raises on one and not the
    other. Everything we store is UTC, so attaching the tzinfo is a statement of
    what the value already means rather than a conversion.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _past_deadline(call: Call, now: datetime) -> bool:
    return call.ended_at is not None and now - _as_utc(call.ended_at) > GIVE_UP_AFTER


def _due_for_poll(call: Call, now: datetime) -> bool:
    if call.last_reconciled_at is None:
        return True
    age = now - _as_utc(call.ended_at) if call.ended_at else timedelta(0)
    interval = _BACKOFF[-1][1]
    for threshold, gap in _BACKOFF:
        if age <= threshold:
            interval = gap
            break
    return now - _as_utc(call.last_reconciled_at) >= interval


def _still_missing(call: Call) -> str:
    """Name what never arrived, rather than always blaming the result.

    Now that engagement is chased too, a call can time out with its result
    already in hand — and "result did not arrive" would then be the screen
    stating something the record contradicts.
    """
    missing = [
        name
        for name, value in (
            ("the result", call.result),
            ("the recording", call.recording_url),
            ("the engagement status", call.engagement_status),
        )
        if not value
    ]
    return f"{' and '.join(missing)} did not arrive within 10 minutes of ending"


def _stop(call: Call, now: datetime, reason: str) -> None:
    call.reconcile_stopped_at = now
    call.reconcile_stopped_reason = reason


# --- demand-driven --------------------------------------------------------

# At most one reconcile per campaign per this many seconds, so a frontend
# polling every 3s across several open tabs cannot stampede Hunar.
DEMAND_INTERVAL_SECONDS = 5.0
_last_demand_run: dict[int, float] = {}


async def reconcile_for_view(
    session: AsyncSession,
    campaign_id: int,
    provider: VoiceProvider | None = None,
) -> ReconcileReport:
    """Called by the campaign-view endpoints before they return data.

    Cron at one-minute granularity is far too coarse for a funnel someone is
    watching, and Render's free tier has no worker process to run anything
    finer. So polling is driven by an observer being present: the person looking
    at the screen is what makes the data move, and the cron is the backstop for
    when nobody is.

    The rate-limit window is per-process memory, same constraint as the webhook
    limiter, and resets on the cold starts the free tier produces. That is fine:
    it exists to damp a polling frontend, not to enforce a quota.
    """
    now = time.monotonic()
    if now - _last_demand_run.get(campaign_id, 0.0) < DEMAND_INTERVAL_SECONDS:
        return ReconcileReport(notes=["skipped, rate limited"])
    _last_demand_run[campaign_id] = now

    active = await reconcile_active_calls(session, campaign_id, provider)
    incomplete = await reconcile_incomplete_calls(session, campaign_id, provider)
    return active.merge(incomplete)


def reset_demand_rate_limit() -> None:
    """Test helper: module state would otherwise leak between tests."""
    _last_demand_run.clear()
