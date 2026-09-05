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
