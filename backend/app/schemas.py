"""Request and response models for our own API.

Separate from `integrations/hunar/types.py`, which describes Hunar's shapes. This
file describes ours, and the two should not be confused: one we control, the
other we discover.
"""

import re
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
