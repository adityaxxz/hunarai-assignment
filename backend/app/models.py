"""All SQLAlchemy models for the project, in one file.

Two conventions worth knowing before reading:

1. Enums we own (candidate source, decision, campaign status) are typed columns
   backed by a Python StrEnum, so SQLAlchemy rejects an unknown value on write.
   Enums Hunar owns (call status, lifecycle status, engagement) are plain `str`
   columns that accept anything. Rejected typing both: if Hunar adds a status
   value we have never seen, webhook ingestion must record it, not blow up.
   `native_enum=False` keeps all of them as VARCHAR rather than Postgres ENUM
   types, so adding a member is a code change, not a migration.
2. Names and values of every enum member are identical uppercase strings, so
   what is in the database is what is in the code.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Postgres gets JSONB, every other dialect gets plain JSON. The only other
# dialect is the in-memory SQLite the tests run on, which is what keeps `pytest`
# from needing a database server. Production DDL is unchanged: the migration
# still emits JSONB.
JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CandidateSource(StrEnum):
    INBOUND_CSV = "INBOUND_CSV"
    SOURCED_PDL = "SOURCED_PDL"
    MANUAL = "MANUAL"


class CampaignKind(StrEnum):
    SCREENING = "SCREENING"
    SOURCING = "SOURCING"


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    DISPATCHING = "DISPATCHING"
    RUNNING = "RUNNING"
    # Hunar accepted some rows and not others. A campaign that claims to be
    # running when half its calls never left is worse than one that says so.
    PARTIALLY_DISPATCHED = "PARTIALLY_DISPATCHED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ScreeningDecision(StrEnum):
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"
    UNDECIDED = "UNDECIDED"


class InterviewStatus(StrEnum):
    BOOKED = "BOOKED"
    RESCHEDULED = "RESCHEDULED"
    ATTENDED = "ATTENDED"
    NO_SHOW = "NO_SHOW"


class CandidateStatus(StrEnum):
    """Intake status only. The screening outcome lives on `calls`, because a
    candidate can be called more than once and the verdict belongs to an attempt,
    not to the person."""

    NEW = "NEW"
    DO_NOT_CALL = "DO_NOT_CALL"


class MessageChannel(StrEnum):
    LOGGED = "LOGGED"
    WHATSAPP = "WHATSAPP"


class MessageStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


def _enum(python_enum: type[StrEnum]) -> Enum:
    # validate_strings is off by default, which lets a plain string sail past the
    # enum and into the column. On, so a typo fails at the write, not at read time.
    return Enum(python_enum, native_enum=False, length=32, validate_strings=True)


class Requisition(Base, TimestampMixin):
    __tablename__ = "requisitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(200))
    # Singular: a Hunar agent speaks exactly one language, so a requisition
    # needing two needs two agents. Storing a list here would imply otherwise.
    language: Mapped[str] = mapped_column(String(32), default="ENGLISH")
    voice_persona: Mapped[str] = mapped_column(String(32), default="NEHA")
    shift: Mapped[str | None] = mapped_column(String(120))
    pay_min: Mapped[int | None]
    pay_max: Mapped[int | None]
    openings: Mapped[int] = mapped_column(default=1)

    # THE source of truth for screening. One list drives three things that must
    # never disagree: the questions the agent asks, the result_schema Hunar
    # extracts against, and the rubric that decides qualification. Stored as the
    # JSON the UI edits rather than normalised into rows, because it is only ever
    # read as a whole. See services/agent_builder.py.
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn, default=list)
    # Per-candidate values the prompt personalises on. Each one must appear as a
    # {token} in the prompt or Hunar will not create the custom variable, and
    # task 9's CSV mapping must supply every one of them.
    candidate_variables: Mapped[list[str]] = mapped_column(JSONColumn, default=list)


class AgentVersion(Base, TimestampMixin):
    """One generated Hunar agent config, kept so a prompt change is traceable to
    the agent id it produced and to the calls made under it."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("requisition_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Null for Module B reachout agents, which belong to a search, not a requisition.
    requisition_id: Mapped[int | None] = mapped_column(
        ForeignKey("requisitions.id"), index=True
    )
    version: Mapped[int] = mapped_column(default=1)

    name: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(32))
    voice_persona: Mapped[str] = mapped_column(String(32))
    persona_name: Mapped[str] = mapped_column(String(64))
    agent_prompt: Mapped[str] = mapped_column(Text)
    objective: Mapped[str] = mapped_column(Text)
    introduction: Mapped[str] = mapped_column(Text)
    result_prompt: Mapped[str] = mapped_column(Text)
    result_schema: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)

    # Null until the config is pushed to Hunar, so an edited-but-unpushed draft
    # is a first-class state rather than something we have to infer.
    hunar_agent_id: Mapped[str | None] = mapped_column(String(64), unique=True)

    # What Hunar COMPUTED, read back after creation — never what we sent. The
    # live capture showed custom_variables came back empty for an agent whose
    # introduction contained {callee_name}, because callee_name is always a
    # required variable and never becomes a custom one. Storing our assumption
    # would have recorded a contract that does not exist.
    custom_variables: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    required_variables: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    result_variables: Mapped[list[str]] = mapped_column(JSONColumn, default=list)


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    requisition_id: Mapped[int | None] = mapped_column(
        ForeignKey("requisitions.id"), index=True
    )
    sourcing_search_id: Mapped[int | None] = mapped_column(
        ForeignKey("sourcing_searches.id"), index=True
    )

    name: Mapped[str] = mapped_column(String(200))
    phone_e164: Mapped[str] = mapped_column(String(20))
    source: Mapped[CandidateSource] = mapped_column(_enum(CandidateSource))
    status: Mapped[CandidateStatus] = mapped_column(
        _enum(CandidateStatus), default=CandidateStatus.NEW
    )
    # Which resolver produced the number, so the UI can badge it. Null for
    # inbound candidates, who supplied their own.
    phone_source: Mapped[str | None] = mapped_column(String(32))

    # Feeds Hunar's custom_data. Must cover every key in the agent's
    # custom_variables or the call create returns 422.
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)

    interview_slot_id: Mapped[int | None] = mapped_column(
        ForeignKey("interview_slots.id"), index=True
    )
    interview_status: Mapped[InterviewStatus | None] = mapped_column(
        _enum(InterviewStatus)
    )

    calls: Mapped[list["Call"]] = relationship(back_populates="candidate")


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[CampaignKind] = mapped_column(_enum(CampaignKind))
    name: Mapped[str] = mapped_column(String(200))
    requisition_id: Mapped[int | None] = mapped_column(
        ForeignKey("requisitions.id"), index=True
    )
    agent_version_id: Mapped[int] = mapped_column(
        ForeignKey("agent_versions.id"), index=True
    )
    # Our tracking id echoed back on every Hunar call, so a call always traces to
    # its batch. Null until dispatch, because it is derived from the row id.
    request_id: Mapped[str | None] = mapped_column(String(64), unique=True)

    # Hunar rejects a partial retry_config, so these two move together or both
    # stay null and the object is omitted from the request entirely.
    max_retry_count: Mapped[int | None]
    retry_interval_hours: Mapped[int | None]

    # Same all-or-nothing rule for guardrails, across all three fields. Times are
    # stored as "HH:MM" strings rather than TIME because Hunar rejects HH:MM:SS,
    # and a TIME column would tempt us to format it back with seconds.
    allowed_days: Mapped[list[str] | None] = mapped_column(JSONColumn)
    earliest_call_time: Mapped[str | None] = mapped_column(String(5))
    last_call_time: Mapped[str | None] = mapped_column(String(5))

    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    from_phone_number: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[CampaignStatus] = mapped_column(
        _enum(CampaignStatus), default=CampaignStatus.DRAFT
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the batch failed as a whole, when it did. Per-call reasons live on the
    # call rows, because a partial failure has both.
    dispatch_error: Mapped[str | None] = mapped_column(Text)

    calls: Mapped[list["Call"]] = relationship(back_populates="campaign")


class Call(Base, TimestampMixin):
    """Our mirror of a Hunar call, plus the screening outcome we derive from it."""

    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    hunar_call_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Set when this specific row never made it to Hunar. Null and no
    # hunar_call_id means still in flight; non-null means we know it failed.
    dispatch_error: Mapped[str | None] = mapped_column(Text)

    # Two separate status fields on purpose. `status` is the current attempt,
    # `lifecycle_status` is the overall state across retries. A call that is
    # NOT_CONNECTED on this attempt but still IN_PROGRESS overall has not failed.
    status: Mapped[str | None] = mapped_column(String(32))
    lifecycle_status: Mapped[str | None] = mapped_column(String(32))
    engagement_status: Mapped[str | None] = mapped_column(String(32))
    answered_by: Mapped[str | None] = mapped_column(String(32))
    call_ended_by: Mapped[str | None] = mapped_column(String(32))
    redial_status: Mapped[str | None] = mapped_column(String(32))

    retry_count: Mapped[int] = mapped_column(default=0)
    retries_left: Mapped[int | None]
    next_retry_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # duration_minutes is deliberately not stored: Hunar sends both and one is
    # the other divided by 60, so keeping it would be a second source of truth.
    duration_seconds: Mapped[float | None]
    user_speech_duration: Mapped[float | None]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Both may stay null forever on a call that never connected.
    recording_url: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)

    decision: Mapped[ScreeningDecision | None] = mapped_column(_enum(ScreeningDecision))
    score: Mapped[float | None]
    override_decision: Mapped[ScreeningDecision | None] = mapped_column(
        _enum(ScreeningDecision)
    )
    override_reason_code: Mapped[str | None] = mapped_column(String(64))
    override_note: Mapped[str | None] = mapped_column(Text)
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Reconciliation bookkeeping. Hunar only sends one status webhook, at the
    # terminal transition, so polling is the primary source of funnel movement
    # rather than a repair mechanism, and it needs to remember what it has done.
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when we stop chasing a result that is never going to arrive. Recorded
    # rather than left pending forever, so "we gave up" is a visible state.
    reconcile_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_stopped_reason: Mapped[str | None] = mapped_column(String(120))

    campaign: Mapped["Campaign"] = relationship(back_populates="calls")
    candidate: Mapped["Candidate"] = relationship(back_populates="calls")


class CallEvent(Base):
    """Raw webhook payloads, written before anything is interpreted."""

    __tablename__ = "call_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable, and that is load-bearing. A webhook can arrive before our own
    # dispatch has written the call row, and the id may not even be in the body.
    # Rejecting the event would make Hunar retry four times and then drop it
    # permanently, so we store it unlinked and let task 6 resolve it.
    hunar_call_id: Mapped[str | None] = mapped_column(String(64), index=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    # The exact bytes Hunar sent, as text, not parsed JSON. The signature is
    # computed over these bytes, so keeping them verbatim is what makes the
    # stored event re-verifiable; a JSONB round-trip reorders keys and drops
    # whitespace. It also means a malformed body is still recorded rather than
    # rejected at the column.
    raw_body: Mapped[str] = mapped_column(Text)

    # SHA-256 of the exact raw request body. Hunar redelivers on any non-2xx and
    # can deliver twice on success, so idempotency is enforced here by the
    # database rather than by a check-then-insert that races itself.
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set once the event has been dealt with, successfully or not. Null means
    # "not yet", which is what orphan resolution sweeps for, so an event whose
    # call row does not exist yet must stay null.
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Non-null with processed_at set means we gave up on this one: the body could
    # not be interpreted. Terminal, not a retry queue, since re-reading an
    # unparseable body produces the same nothing.
    processing_error: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Who changed a machine decision, when, and what it was before.

    An eleventh table, beyond the ten notes.md lists, because a recruiter
    override is the one write in this system that a human makes against the
    machine's own conclusion. Storing only the new value on `calls` would lose
    what was overruled, and "the model said REJECTED and a person said otherwise"
    is exactly the record a hiring process has to be able to produce later.

    Generic in shape but currently single-writer: the override endpoint is the
    only thing that appends to it. `actor` is a plain string because there is no
    authentication in this build — see the open question in current-status.md.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[int] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(120))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    # Whole snapshots rather than a field-level diff: the interesting question is
    # "what did the machine conclude", and that is decision plus score together.
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InterviewSlot(Base, TimestampMixin):
    __tablename__ = "interview_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    requisition_id: Mapped[int] = mapped_column(
        ForeignKey("requisitions.id"), index=True
    )
    location: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity: Mapped[int]
    booked_count: Mapped[int] = mapped_column(default=0)


class Message(Base, TimestampMixin):
    """Outbox row. Written whether the channel actually sends or only logs, so the
    in-app outbox is the same artifact in demo mode and in live mode."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id"), index=True
    )
    channel: Mapped[MessageChannel] = mapped_column(_enum(MessageChannel))
    recipient: Mapped[str] = mapped_column(String(20))
    template: Mapped[str] = mapped_column(String(64))
    variables: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    trigger_event: Mapped[str] = mapped_column(String(64))
    status: Mapped[MessageStatus] = mapped_column(
        _enum(MessageStatus), default=MessageStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DncEntry(Base, TimestampMixin):
    __tablename__ = "dnc_list"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True)
    reason: Mapped[str | None] = mapped_column(String(200))


class SourcingSearch(Base, TimestampMixin):
    __tablename__ = "sourcing_searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    jd_text: Mapped[str] = mapped_column(Text)
    # Kept so the interview answer to "did the LLM write this query?" is a row,
    # not a guess. The UI lets the user edit it before the search runs.
    generated_query: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    provider: Mapped[str] = mapped_column(String(32))
    result_count: Mapped[int] = mapped_column(default=0)
