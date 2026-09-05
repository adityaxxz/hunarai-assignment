"""Request and response models for our own API.

Separate from `integrations/hunar/types.py`, which describes Hunar's shapes. This
file describes ours, and the two should not be confused: one we control, the
other we discover.
"""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.integrations.hunar.types import Language, VoicePersona

# snake_case, because the key becomes a result_schema key and a JSON field name
# downstream. A key with a space or a dash is a key someone has to quote forever.
_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


class Criterion(BaseModel):
    """One screening question. The unit that drives prompt, schema and rubric."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY_PATTERN, max_length=64)
    question: str = Field(min_length=3, max_length=300)
    # Only the two types the capture proved Hunar honours: a declared "boolean"
    # came back as a real JSON boolean, a "string" as a string.
    type: Literal["boolean", "string"] = "boolean"
    knockout: bool = False
    weight: int = Field(default=1, ge=0, le=100)
    # What a passing answer looks like. Optional for a string criterion, where
    # any non-empty answer counts unless a specific value is required.
    expected: bool | str | None = None

    @model_validator(mode="after")
    def _check_expected(self) -> "Criterion":
        if self.type == "boolean":
            if self.expected is None:
                self.expected = True
            if not isinstance(self.expected, bool):
                raise ValueError(f"criterion '{self.key}' is boolean, so expected must be true or false")
        elif isinstance(self.expected, bool):
            raise ValueError(f"criterion '{self.key}' is a string, so expected cannot be a boolean")
        return self


class RequisitionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["SCREENING", "SOURCING"] = "SCREENING"
    title: str = Field(min_length=2, max_length=200)
    location: str = Field(min_length=2, max_length=200)
    language: Language = Language.ENGLISH
    voice_persona: VoicePersona = VoicePersona.NEHA
    shift: str | None = Field(default=None, max_length=120)
    pay_min: int | None = Field(default=None, ge=0)
    pay_max: int | None = Field(default=None, ge=0)
    openings: int = Field(default=1, ge=1)
    criteria: list[Criterion] = Field(default_factory=list)
    candidate_variables: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "RequisitionBase":
        keys = [c.key for c in self.criteria]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(f"duplicate criterion keys: {', '.join(sorted(duplicates))}")
        for name in self.candidate_variables:
            if not re.match(_KEY_PATTERN, name):
                raise ValueError(f"candidate variable '{name}' must be snake_case")
        # These two are always supplied by Hunar and never become custom
        # variables, so declaring one would create a token that can never be
        # filled. Observed in the capture.
        reserved = {"callee_name", "mobile_number"} & set(self.candidate_variables)
        if reserved:
            raise ValueError(
                f"{', '.join(sorted(reserved))} is always provided by Hunar and cannot be a candidate variable"
            )
        if self.pay_min is not None and self.pay_max is not None and self.pay_min > self.pay_max:
            raise ValueError("pay_min cannot exceed pay_max")
        return self


class RequisitionCreate(RequisitionBase):
    pass


class RequisitionUpdate(RequisitionBase):
    pass


class RequisitionRead(RequisitionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class AgentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requisition_id: int | None
    version: int
    name: str
    hunar_agent_id: str | None
    agent_prompt: str
    objective: str
    introduction: str
    result_prompt: str
    result_schema: dict[str, Any]
    # Read back from Hunar after creation, not what we sent.
    custom_variables: list[str]
    required_variables: list[str]
    result_variables: list[str]


class CriterionOutcomeRead(BaseModel):
    key: str
    question: str
    knockout: bool
    weight: int
    value: Any
    status: Literal["pass", "fail", "unknown"]
    reason: str


class EvaluationRead(BaseModel):
    decision: str
    score: float
    reasons: list[CriterionOutcomeRead]


class MappingProposal(BaseModel):
    """The upload step's answer. Nothing has been written yet."""

    headers: list[str]
    row_count: int
    # null means "we could not tell": the recruiter has to say which column it is.
    mapping: dict[str, str | None]
    required_variables: list[str]
    preview: list[dict[str, str]]


class CandidateManualCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=5, max_length=25)
    custom_fields: dict[str, str] = Field(default_factory=dict)


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requisition_id: int | None
    name: str
    phone_e164: str
    source: str
    status: str
    custom_fields: dict[str, Any]


class CandidatePage(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CandidateRead]


class PreflightReport(BaseModel):
    """What would happen if a campaign launched now, stated pessimistically."""

    candidates: int
    dialable: int
    excluded: list[dict[str, Any]]
    agent_version_id: int | None
    hunar_agent_id: str | None
    required_variables: list[str]
    ready: bool
    blockers: list[str]


class GuardrailsIn(BaseModel):
    """Loose on purpose. `services/campaign.py` does the checking so it can report
    every problem at once with a message a recruiter can act on, rather than
    Pydantic reporting the first field that failed."""

    model_config = ConfigDict(extra="forbid")

    allowed_days: list[str] = Field(default_factory=list)
    earliest_call_time: str = ""
    last_call_time: str = ""


class RetryConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retry_count: int | None = None
    retry_interval_hours: int | None = None


class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requisition_id: int
    name: str = Field(min_length=1, max_length=200)
    # SOURCING marks a batch that reaches people who did not apply. It changes
    # nothing about dispatch — same endpoint, same rows, same webhooks — and
    # exists so the funnel can be filtered and reported on separately.
    kind: Literal["SCREENING", "SOURCING"] = "SCREENING"
    # Defaults to the newest agent version that has actually been pushed to Hunar.
    agent_version_id: int | None = None
    guardrails: GuardrailsIn | None = None
    retry_config: RetryConfigIn | None = None
    timezone: str = "Asia/Kolkata"
    from_phone_number: str | None = None


class CallRead(BaseModel):
    id: int
    candidate_id: int
    candidate_name: str
    hunar_call_id: str | None
    stage: str
    status: str | None
    lifecycle_status: str | None
    engagement_status: str | None
    retry_count: int
    retries_left: int | None
    next_retry_scheduled_at: datetime | None
    dispatch_error: str | None
    has_result: bool
    # Both surfaced because terminal is not the same as finished. Hunar's API is
    # eventually consistent after COMPLETED, so a settled call may still gain a
    # result — or may never gain one. Without these the UI cannot tell "still
    # settling" from "we stopped chasing it", and it guesses.
    reconcile_stopped_at: datetime | None
    reconcile_stopped_reason: str | None


class CampaignPage(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CallRead]


class CampaignDetail(BaseModel):
    id: int
    name: str
    requisition_id: int | None
    agent_version_id: int
    request_id: str | None
    status: str
    dispatch_error: str | None
    dispatched_at: datetime | None
    total_calls: int
    funnel: dict[str, int]
    # Stated because a call outside the window is scheduled, not rejected, and a
    # recruiter who hears nothing will otherwise assume the product is broken.
    dialling_now: bool
    dial_starts_at: datetime | None
    dial_window_note: str
    estimate: dict[str, Any]


# --- call detail -----------------------------------------------------------

# A closed vocabulary, because the point of a reason code is that it can be
# counted. Free text alone tells you a recruiter disagreed; it does not tell you
# whether the agent keeps mishearing the same question.
OverrideReasonCode = Literal[
    "SPOKE_TO_CANDIDATE",
    "AGENT_MISHEARD",
    "RESULT_INCOMPLETE",
    "REQUIREMENTS_CHANGED",
    "OTHER",
]

OVERRIDE_REASON_LABELS: dict[str, str] = {
    "SPOKE_TO_CANDIDATE": "I spoke to the candidate myself",
    "AGENT_MISHEARD": "The agent misheard or mis-recorded an answer",
    "RESULT_INCOMPLETE": "The call ended before the answers were complete",
    "REQUIREMENTS_CHANGED": "The role requirements changed",
    "OTHER": "Other",
}


class OverrideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["QUALIFIED", "REJECTED", "UNDECIDED"]
    reason_code: OverrideReasonCode
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _note_required_for_other(self) -> "OverrideCreate":
        if self.reason_code == "OTHER" and not (self.note or "").strip():
            # OTHER with no note is an override with no recorded reason, which is
            # the one thing an audit trail must not contain.
            raise ValueError("a note is required when the reason code is OTHER")
        return self


class CallEventRead(BaseModel):
    """One row of the timeline. The raw body is deliberately not returned.

    It can carry a phone number and the S3 recording URL, and this endpoint feeds
    a browser. What the timeline is for is *when each event arrived and whether
    it was processed* — that is what makes the webhook and reconcile behaviour
    inspectable rather than theoretical.
    """

    id: int
    event_type: str
    received_at: datetime
    processed_at: datetime | None
    processing_error: str | None
    # False for an event that arrived before its call row existed and was
    # adopted later. Worth seeing: it is the orphan path actually happening.
    linked: bool


class OverrideRead(BaseModel):
    decision: str
    reason_code: str
    reason_label: str
    note: str | None
    at: datetime


class CallDetail(BaseModel):
    """Everything the candidate detail screen needs, in one response.

    One endpoint rather than five, because every part of it is read from the same
    call row and the screen is useless with any piece missing.
    """

    id: int
    campaign_id: int
    hunar_call_id: str | None
    candidate_id: int
    candidate_name: str
    candidate_phone: str
    candidate_custom_fields: dict[str, Any]
    requisition_id: int | None
    requisition_title: str | None

    stage: str
    status: str | None
    lifecycle_status: str | None
    engagement_status: str | None
    answered_by: str | None
    call_ended_by: str | None
    redial_status: str | None
    retry_count: int
    retries_left: int | None
    next_retry_scheduled_at: datetime | None
    duration_seconds: float | None
    user_speech_duration: float | None
    started_at: datetime | None
    ended_at: datetime | None
    dispatch_error: str | None
    last_reconciled_at: datetime | None
    reconcile_stopped_at: datetime | None
    reconcile_stopped_reason: str | None

    result: dict[str, Any] | None
    # Recomputed on every read from the requisition's current criteria, never
    # stamped on the row. See services/evaluation.py for why.
    evaluation: EvaluationRead | None
    override: OverrideRead | None
    # What is acted on: the override where one exists, otherwise the computed
    # decision. Resolved here so no consumer has to re-derive the precedence.
    effective_decision: str | None

    recording_available: bool
    # True when the bytes the proxy serves are a generated silence rather than a
    # real call. Said out loud so the player is never silently a lie.
    recording_simulated: bool

    timeline: list[CallEventRead]


# --- sourcing (Module B) ---------------------------------------------------

# Mirrors services/contact_resolution.RESOLVER_LABELS. Re-exported here so the
# router imports one module for its response shapes.
RESOLVER_LABELS: dict[str, str] = {
    "provider": "From the search provider",
    "fixture": "Demo number",
    "unresolved": "No number found",
}


class SourcingSearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jd_text: str = Field(min_length=20, max_length=20_000)


class SourcingSearchOut(BaseModel):
    id: int
    jd_text: str
    query: dict[str, Any]
    # "gemini" or "fallback". Shown on screen, because a recruiter who believes a
    # model read their JD will trust a keyword-extracted query more than it earns.
    query_source: str
    note: str
    titles: list[str]
    locations: list[str]
    provider: str


class SearchRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The edited query, not the generated one. The model proposes; the person
    # sending this request decides.
    query: dict[str, Any]
    limit: int = Field(default=10, ge=1, le=25)


class SourcingProfileOut(BaseModel):
    full_name: str
    headline: str | None
    current_title: str | None
    current_company: str | None
    location: str | None
    linkedin_url: str | None
    dedupe_key: str
    phone_e164: str | None
    resolver: str
    resolver_label: str
    resolver_detail: str
    already_a_candidate: bool
    do_not_call: bool


class SearchRunOut(BaseModel):
    search_id: int
    provider: str
    total_available: int | None
    notes: list[str]
    dialable: int
    profiles: list[SourcingProfileOut]


class ProfileIn(BaseModel):
    """One selected profile, sent back for import. Echoed from the search result
    rather than re-fetched, because re-running the query would spend a second set
    of credits to get the same rows."""

    model_config = ConfigDict(extra="ignore")

    full_name: str = Field(min_length=1, max_length=200)
    headline: str | None = None
    current_title: str | None = None
    current_company: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    phone_e164: str | None = None
    resolver: str = "unresolved"


class SourcingImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    location: str = Field(min_length=2, max_length=200)
    language: Language = Language.ENGLISH
    voice_persona: VoicePersona = VoicePersona.NEHA
    profiles: list[ProfileIn] = Field(default_factory=list)
    # Not a formality. Nobody in this list applied, so dispatch is gated on a
    # person having looked at it. See routers/sourcing.import_selected.
    confirm: bool = False


class SourcingImportOut(BaseModel):
    requisition_id: int
    imported: int
    skipped: list[dict[str, str]]


class SourcingInsights(BaseModel):
    campaign_id: int
    kind: str
    total_calls: int
    answered: int
    interested: int
    # Null rather than zero when nothing has been answered yet: 0% interest and
    # "nobody has picked up" are different facts.
    interest_rate: float | None
    notice_period: dict[str, int]
    objections: dict[str, int]
    callback_times: list[str]
