"""Request and response shapes for the Hunar Voice API.

The asymmetry that runs through this file: **request models are strict, response
models are lenient.** A request we get wrong should fail here rather than come
back as a 422 in front of a recruiter. A response we did not expect should still
parse, because Hunar can add a status value or a field at any time and our
webhook and reconciliation paths must keep working when they do.

So the enums below are used as *input* types only. On responses, every field
Hunar owns the vocabulary of is a plain `str`, matching the same split already
made in `app/models.py`. The enums are still the right way to compare against
one, because `StrEnum` members equal their own string value:

    if call.status == CallStatus.COMPLETED:   # works on a plain str
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Language(StrEnum):
    ENGLISH = "ENGLISH"
    HINDI = "HINDI"
    TAMIL = "TAMIL"
    TELUGU = "TELUGU"
    KANNADA = "KANNADA"
    MARATHI = "MARATHI"
    MALAYALAM = "MALAYALAM"
    GUJARATI = "GUJARATI"
    BENGALI = "BENGALI"
    TURKISH = "TURKISH"
    ARABIC = "ARABIC"
    SPANISH = "SPANISH"


class VoicePersona(StrEnum):
    NEHA = "NEHA"
    ROY = "ROY"
    ZOE = "ZOE"
    SAM = "SAM"
    MIRA = "MIRA"
    EESHA = "EESHA"


class Weekday(StrEnum):
    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"
    SUN = "SUN"


class CallStatus(StrEnum):
    """State of the current dial attempt."""

    NOT_STARTED = "NOT_STARTED"
    SCHEDULED = "SCHEDULED"
    INITIATED = "INITIATED"
    RINGING = "RINGING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    NOT_CONNECTED = "NOT_CONNECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class LifecycleStatus(StrEnum):
    """State of the call overall, across retries. A call that is NOT_CONNECTED on
    this attempt but IN_PROGRESS here has not failed, it is waiting to redial."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    NOT_CONNECTED = "NOT_CONNECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EngagementStatus(StrEnum):
    ENGAGED = "ENGAGED"
    NOT_ENGAGED = "NOT_ENGAGED"


class AnsweredBy(StrEnum):
    HUMAN = "HUMAN"
    MACHINE = "MACHINE"
    UNKNOWN = "UNKNOWN"


class CallEndedBy(StrEnum):
    AGENT = "AGENT"
    USER = "USER"
    UNKNOWN = "UNKNOWN"


_HHMM = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


class RetryConfig(BaseModel):
    """Hunar rejects a partial retry_config and an empty `{}`, so both fields are
    required here. Omitting retries means leaving the whole object off the
    request, not sending one field; disabling them means both set to 0."""

    model_config = ConfigDict(extra="forbid")

    max_retry_count: int = Field(ge=0, le=10)
    retry_interval_hours: Literal[0, 3, 6, 9, 12, 24]


class Guardrails(BaseModel):
    """All three fields are required together, same all-or-nothing rule."""

    model_config = ConfigDict(extra="forbid")

    allowed_days: list[Weekday] = Field(min_length=3)
    # Hunar accepts HH:MM and rejects the legacy HH:MM:SS, so the pattern is
    # exact rather than a permissive time parse.
    earliest_call_time: str = Field(pattern=_HHMM)
    last_call_time: str = Field(pattern=_HHMM)

    @model_validator(mode="after")
    def _check_window(self) -> "Guardrails":
        if len(set(self.allowed_days)) < 3:
            raise ValueError("allowed_days needs at least 3 distinct days")
        earliest = _minutes(self.earliest_call_time)
        latest = _minutes(self.last_call_time)
        if earliest >= latest:
            raise ValueError("earliest_call_time must be before last_call_time")
        if latest - earliest < 180:
            raise ValueError("calling window must be at least 3 hours wide")
        return self


class CallbackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_status_callback_url: str | None = None
    call_recording_callback_url: str | None = None
    call_result_callback_url: str | None = None
    call_summary_callback_url: str | None = None


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=64)
    language: Language
    voice_persona: VoicePersona
    persona_name: str | None = None
    agent_prompt: str
    objective: str
    introduction: str
    result_prompt: str
    result_schema: dict[str, Any] = Field(default_factory=dict)


# PUT /agents/{id}/ is a full replace, so the update body is the create body.
AgentUpdate = AgentCreate


class Agent(BaseModel):
    """Agent as Hunar returns it. Only `id` and `name` are relied on; the rest
    carry defaults so a trimmed list payload parses as happily as a full detail."""

    id: str
    name: str
    language: str | None = None
    voice_persona: str | None = None
    persona_name: str | None = None
    voice_name: str | None = None
    summary: str | None = None
    status: str | None = None
    logo: str | None = None
    agent_code: str | None = None
    agent_prompt: str | None = None
    objective: str | None = None
    introduction: str | None = None
    result_prompt: str | None = None
    silence_response: str | None = None
    conclusion: str | None = None
    result_schema: dict[str, Any] | None = None
    # Every key here must appear in a call's custom_data or the create 422s.
    custom_variables: list[str] = Field(default_factory=list)
    required_variables: list[str] = Field(default_factory=list)
    result_variables: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


_REQUEST_ID = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")


class CallCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    callee_name: str
    mobile_number: str
    custom_data: dict[str, Any] | None = None
    from_phone_number: str | None = None
    request_id: str | None = _REQUEST_ID
    retry_config: RetryConfig | None = None
    guardrails: Guardrails | None = None
    timezone: str | None = None
    callback_config: CallbackConfig | None = None


class BulkCallRecipient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callee_name: str
    mobile_number: str
    custom_data: dict[str, Any] | None = None


class BulkCallCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    data: list[BulkCallRecipient] = Field(min_length=1, max_length=10_000)
    request_id: str | None = _REQUEST_ID
    from_phone_number: str | None = None
    retry_config: RetryConfig | None = None
    guardrails: Guardrails | None = None
    timezone: str | None = None
    callback_config: CallbackConfig | None = None
    remove_invalid_rows: bool = True
    remove_duplicate_phone_numbers: bool = True


class Call(BaseModel):
    """Call as Hunar returns it, from both the create and the read endpoints.

    Everything except `id` is optional and every status is a plain `str`, so an
    unfamiliar value parses instead of raising in the middle of webhook
    ingestion. The nested config objects are left as raw dicts for the same
    reason: reusing the strict request models here would mean a Hunar-side shape
    change turns a readable call into a validation error, and the retry numbers
    we actually act on are the flat ones below.
    """

    id: str
    callee_name: str | None = None
    mobile_number: str | None = None
    from_phone_number: str | None = None
    agent_id: str | None = None
    campaign_id: str | None = None
    language: str | None = None

    status: str | None = None
    lifecycle_status: str | None = None
    engagement_status: str | None = None
    answered_by: str | None = None
    call_ended_by: str | None = None
    redial_status: str | None = None

    # Note the name. The request sends retry_config.max_retry_count; the response
    # returns max_retries. The API is not symmetric here and assuming it is
    # silently reads None.
    max_retries: int | None = None
    retry_count: int | None = None
    retries_left: int | None = None
    next_retry_scheduled_at: datetime | None = None

    recording_url: str | None = None
    result: dict[str, Any] | None = None
    custom_data: dict[str, Any] | None = None
    system_data: dict[str, Any] | None = None

    duration_minutes: float | None = None
    duration_seconds: float | None = None
    user_speech_duration: float | None = None

    request_id: str | None = None
    timezone: str | None = None
    callback_config: dict[str, Any] | None = None
    retry_config: dict[str, Any] | None = None
    guardrails: dict[str, Any] | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    triggered_by: str | None = None


class PhoneNumber(BaseModel):
    id: str
    phone_number: str
    allowed_countries: list[str] = Field(default_factory=list)
    country_code: str | None = None
    is_default: bool = False
    is_validated: bool = False
    telephony_provider_id: str | None = None
    provider: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Hunar's list envelope. `next` and `previous` are absolute URLs."""

    count: int = 0
    next: str | None = None
    previous: str | None = None
    results: list[T] = Field(default_factory=list)
