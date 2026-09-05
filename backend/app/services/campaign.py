"""Campaign validation, dial-window maths and cost estimation.

Everything here runs **before** the endpoint that spends money, and every failure
comes back as a sentence a recruiter can act on rather than a vendor status code.
Hunar's own errors are accurate but useless at the point of use: "Minimum allowed
earliest_call_time is 08:00." arrives after the launch button, with no indication
of which field or what to change it to.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# The org calling window is 08:00 to 21:00. **Both bounds were learned from 400s
# on live dispatch attempts, not from any documentation**, and each one cost a
# failed campaign to find:
#   400 {"message": "Minimum allowed earliest_call_time is 08:00."}
#   400 {"message": "Maximum allowed last_call_time is 21:00."}
# Section 10 of notes.md says guardrails may exceed the org default when
# from_phone_number is supplied — that exemption is unavailable to us:
# GET /numbers/ returns count 0 for our key, even though Hunar populates a
# from_phone_number on the call automatically. See fixtures/observed_shapes.md.
ORG_EARLIEST_CALL_TIME = time(8, 0)
ORG_LATEST_CALL_TIME = time(21, 0)

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MIN_DISTINCT_DAYS = 3
MIN_WINDOW = timedelta(hours=3)
VALID_RETRY_INTERVALS = (0, 3, 6, 9, 12, 24)

# From the one connected call we captured: 30 seconds, 7.46s of candidate speech.
# A single sample, and said as much wherever it is used.
OBSERVED_CONNECTED_CALL_SECONDS = 30


class CampaignValidationError(Exception):
    """Carries every problem, not the first one found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _parse_hhmm(value: str) -> time | None:
    try:
        hours, minutes = value.split(":")
        if len(value) != 5 or len(hours) != 2 or len(minutes) != 2:
            return None
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return None


def validate_guardrails(guardrails: dict[str, Any] | None) -> list[str]:
    """All-or-nothing, and every rule checked rather than short-circuiting."""
    if guardrails is None:
        return []

    problems: list[str] = []
    missing = [
        f for f in ("allowed_days", "earliest_call_time", "last_call_time")
        if not guardrails.get(f)
    ]
    if missing:
        # Hunar rejects a partial guardrails object outright, so a half-filled
        # form has to fail here rather than at dispatch.
        problems.append(
            "guardrails are all-or-nothing: " + ", ".join(missing) + " must be set too, "
            "or leave guardrails off entirely to use the organisation default"
        )
        return problems

    days = list(guardrails["allowed_days"])
    unknown = [d for d in days if d not in WEEKDAYS]
    if unknown:
        problems.append(f"unknown day(s) {', '.join(unknown)}; use MON to SUN")
    if len(set(days)) < MIN_DISTINCT_DAYS:
        problems.append(
            f"allowed_days needs at least {MIN_DISTINCT_DAYS} distinct days, got "
            f"{len(set(days))}"
        )
    if len(days) != len(set(days)):
        problems.append("allowed_days must not repeat a day")

    earliest = _parse_hhmm(guardrails["earliest_call_time"])
    latest = _parse_hhmm(guardrails["last_call_time"])
    if earliest is None:
        problems.append(
            f"earliest_call_time {guardrails['earliest_call_time']!r} must be HH:MM; "
            "Hunar rejects HH:MM:SS"
        )
    if latest is None:
        problems.append(
            f"last_call_time {guardrails['last_call_time']!r} must be HH:MM; "
            "Hunar rejects HH:MM:SS"
        )
    if earliest is None or latest is None:
        return problems

    if earliest < ORG_EARLIEST_CALL_TIME:
        problems.append(
            f"earliest_call_time cannot be before {ORG_EARLIEST_CALL_TIME:%H:%M}; the "
            "organisation does not permit calling earlier, and supplying a "
            "from_phone_number does not lift that for this account"
        )
    if latest > ORG_LATEST_CALL_TIME:
        problems.append(
            f"last_call_time cannot be after {ORG_LATEST_CALL_TIME:%H:%M}; the "
            "organisation does not permit calling later, and supplying a "
            "from_phone_number does not lift that for this account"
        )
    if earliest >= latest:
        problems.append("earliest_call_time must be before last_call_time")
    else:
        width = datetime.combine(date.min, latest) - datetime.combine(date.min, earliest)
        if width < MIN_WINDOW:
            problems.append(
                f"the calling window must be at least {MIN_WINDOW.seconds // 3600} hours "
                f"wide, got {width.seconds // 3600}h{(width.seconds // 60) % 60:02d}m"
            )
    return problems


def validate_retry_config(retry_config: dict[str, Any] | None) -> list[str]:
    if retry_config is None:
        return []

    problems: list[str] = []
    count = retry_config.get("max_retry_count")
    interval = retry_config.get("retry_interval_hours")
    if count is None or interval is None:
        # Hunar rejects a partial object and an empty one; both fields together
        # or no object at all. Disabling retries means both set to zero.
        problems.append(
            "retry_config is all-or-nothing: set both max_retry_count and "
            "retry_interval_hours, or omit retry_config entirely. To disable "
            "retries, set both to 0"
        )
        return problems

    if not isinstance(count, int) or not 0 <= count <= 10:
        problems.append(f"max_retry_count must be between 0 and 10, got {count}")
    if interval not in VALID_RETRY_INTERVALS:
        problems.append(
            "retry_interval_hours must be one of "
            f"{', '.join(str(v) for v in VALID_RETRY_INTERVALS)}, got {interval}"
        )
    return problems


def validate_campaign(
    guardrails: dict[str, Any] | None, retry_config: dict[str, Any] | None
) -> None:
    problems = validate_guardrails(guardrails) + validate_retry_config(retry_config)
    if problems:
        raise CampaignValidationError(problems)


@dataclass
class DialWindow:
    starts_at: datetime | None
    dialling_now: bool
    explanation: str


def next_dial_start(
    guardrails: dict[str, Any] | None,
    timezone_name: str,
    now: datetime | None = None,
) -> DialWindow:
    """When calls will actually start ringing.

    This exists because a call placed outside the allowed window is **accepted and
    SCHEDULED, not rejected**. A recruiter who sees "launched", hears nothing, and
    is not told why will conclude the product is broken — and the honest answer,
    "these will start at 08:00 tomorrow", is a fact we already have.
    """
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return DialWindow(None, False, f"unknown timezone {timezone_name!r}")

    current = (now or datetime.now(zone)).astimezone(zone)

    if guardrails is None:
        return DialWindow(
            None,
            False,
            "No guardrails set, so the organisation's default calling window "
            f"applies ({ORG_EARLIEST_CALL_TIME:%H:%M} to {ORG_LATEST_CALL_TIME:%H:%M} "
            f"{timezone_name}). Calls outside it are scheduled rather than rejected.",
        )

    days = set(guardrails["allowed_days"])
    earliest = _parse_hhmm(guardrails["earliest_call_time"])
    latest = _parse_hhmm(guardrails["last_call_time"])
    if earliest is None or latest is None:
        return DialWindow(None, False, "calling window could not be read")

    today_allowed = WEEKDAYS[current.weekday()] in days
    if today_allowed and earliest <= current.time() <= latest:
        return DialWindow(
            current, True,
            f"Inside the calling window now ({earliest:%H:%M}-{latest:%H:%M} "
            f"{timezone_name}), so dialling starts immediately.",
        )

    for offset in range(0, 8):
        day = current.date() + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] not in days:
            continue
        start = datetime.combine(day, earliest, tzinfo=zone)
        if start >= current:
            return DialWindow(
                start, False,
                f"Outside the calling window, so Hunar will schedule these calls "
                f"rather than reject them. Dialling starts {start:%A %d %b at %H:%M} "
                f"{timezone_name}.",
            )
    return DialWindow(None, False, "no allowed day found in the next week")


def estimate(dialable: int, retry_config: dict[str, Any] | None) -> dict[str, Any]:
    """Deliberately not a cost figure.

    We have no per-minute rate for this account — `GET /numbers/` returns nothing
    and no pricing is exposed on the API — so quoting money would be inventing a
    number that looks authoritative. What we can state is how many calls will be
    attempted and roughly how much talk time that implies, with the source of the
    duration said out loud: one captured call.
    """
    max_retries = (retry_config or {}).get("max_retry_count", 0) or 0
    worst_case_attempts = dialable * (1 + max_retries)
    connected_seconds = dialable * OBSERVED_CONNECTED_CALL_SECONDS

    return {
        "calls_to_place": dialable,
        "worst_case_attempts": worst_case_attempts,
        "estimated_talk_minutes_if_all_connect": round(connected_seconds / 60, 1),
        "assumptions": [
            f"{OBSERVED_CONNECTED_CALL_SECONDS}s per connected call, from the single "
            "call captured during development",
            f"worst case assumes every call uses all {max_retries} retries",
        ],
        "unknown": [
            "the per-minute rate for this account is not exposed by the API, so no "
            "monetary cost is estimated",
            "the connect rate is unknown, so actual talk time will be lower than the "
            "figure above",
        ],
    }


@dataclass(frozen=True)
class DispatchOutcome:
    """What actually happened to a campaign's calls, as plain counts.

    Deliberately not the ORM rows. The note below is the fourth attempt in this
    project to stop a screen asserting something the system already knows is
    false, and the previous three all failed the same way: the sentence was
    computed from what was *requested* while the truth sat one query away. Taking
    the outcome as an explicit argument makes it impossible to describe a
    campaign without first having looked at it.
    """

    status: str
    dispatch_error: str | None = None
    # Calls that have left the queue: Hunar has them and they have begun.
    dialled: int = 0
    # Dispatched, accepted, and still waiting for the calling window to open.
    waiting: int = 0
    # Rows Hunar refused. These will never be placed.
    not_dispatched: int = 0
    first_dialled_at: datetime | None = None


def describe_dialling(
    outcome: DispatchOutcome,
    guardrails: dict[str, Any] | None,
    timezone_name: str,
    now: datetime | None = None,
) -> DialWindow:
    """The single place a campaign's dialling is put into words.

    One function rather than a branch per status scattered across the router,
    because the failure mode here is a status nobody thought about inheriting a
    sentence written for a different one. Every path runs through the same three
    questions: has anything dialled, is anything still waiting, was anything
    refused.
    """
    if outcome.status == "FAILED":
        return DialWindow(
            None,
            False,
            "Nothing was dispatched, so no calls are scheduled. Hunar rejected the "
            f"request: {outcome.dispatch_error or 'no reason was given'}",
        )

    upcoming = next_dial_start(guardrails, timezone_name, now)
    refused = _refused_clause(outcome)

    if outcome.dialled == 0:
        # Nothing has been placed, so the future tense is the honest one and
        # `next_dial_start` is describing a window that still matters.
        note = " ".join(filter(None, [upcoming.explanation, refused]))
        return DialWindow(upcoming.starts_at, upcoming.dialling_now, note)

    # Something has already been dialled. Whatever the guardrails say, dialling
    # did not start in the future.
    parts = [_started_clause(outcome, timezone_name)]
    if outcome.waiting:
        parts.append(_waiting_clause(outcome, upcoming, timezone_name))
    if refused:
        parts.append(refused)

    return DialWindow(
        upcoming.starts_at if outcome.waiting else None,
        False,
        " ".join(parts),
    )


def _started_clause(outcome: DispatchOutcome, timezone_name: str) -> str:
    if outcome.first_dialled_at is None:
        # Hunar reports RINGING before it reports a start time, so a call can be
        # demonstrably dialling with no timestamp to quote yet.
        return "Dialling has started."
    try:
        local = outcome.first_dialled_at.astimezone(ZoneInfo(timezone_name))
    except (ZoneInfoNotFoundError, ValueError):
        local = outcome.first_dialled_at
    return f"Dialling started {local:%A %d %b at %H:%M} {timezone_name}."


def _waiting_clause(
    outcome: DispatchOutcome, upcoming: DialWindow, timezone_name: str
) -> str:
    count = outcome.waiting
    subject = f"{count} call is" if count == 1 else f"{count} calls are"
    if upcoming.dialling_now:
        return f"{subject} still queued, inside the calling window now."
    if upcoming.starts_at:
        return (
            f"{subject} still waiting for the calling window, which next opens "
            f"{upcoming.starts_at:%A %d %b at %H:%M} {timezone_name}."
        )
    return f"{subject} still queued."


def _refused_clause(outcome: DispatchOutcome) -> str:
    if not outcome.not_dispatched:
        return ""
    count = outcome.not_dispatched
    subject = f"{count} call was" if count == 1 else f"{count} calls were"
    reason = outcome.dispatch_error or "Hunar did not accept them"
    return f"{subject} never dispatched: {reason}"
