"""Turn Hunar's structured `result` into a qualification decision.

Pure functions. No database, no I/O, no clock. Called from the API layer and
**never** from `call_state`, which is deliberate: a redelivered webhook or a
reconciliation poll must not re-decide a candidate a recruiter has already
overridden. The decision is derived on read, not stamped on write.

It reads the structured `result` object and nothing else. There is no free text
anywhere in this module, which is the point of Hunar's maker-checker second pass
— the risk was never the hallucination, it was the downstream system quietly
acting on a mis-parsed sentence.
"""

from dataclasses import dataclass
from typing import Any, Literal

from app.models import ScreeningDecision
from app.schemas import Criterion

Status = Literal["pass", "fail", "unknown"]


@dataclass(frozen=True)
class CriterionOutcome:
    key: str
    question: str
    knockout: bool
    weight: int
    value: Any
    status: Status
    reason: str


@dataclass(frozen=True)
class Evaluation:
    decision: ScreeningDecision
    score: float
    reasons: list[CriterionOutcome]


def evaluate(result: dict[str, Any] | None, requisition: Any) -> Evaluation:
    """Decide, score, and explain.

    Decision rules, in order:

    1. Any **failed** knockout disqualifies, whatever the score. A rider without
       a licence does not become hireable by answering everything else well.
    2. Any **unknown** knockout leaves it undecided. A 30-second call that never
       reached the licence question has not established anything, and treating
       silence as a pass is how an unqualified candidate reaches an interview
       slot.
    3. Otherwise qualified.

    Note what is deliberately absent: there is no score threshold. No field on a
    requisition defines one, so any cutoff here would be policy this module
    invented. The score ranks candidates; the knockouts decide them.
    """
    values = result or {}
    criteria = [Criterion.model_validate(c) for c in requisition.criteria]
    outcomes = [_assess(c, values) for c in criteria]

    knockouts = [o for o in outcomes if o.knockout]
    if any(o.status == "fail" for o in knockouts):
        decision = ScreeningDecision.REJECTED
    elif any(o.status == "unknown" for o in knockouts):
        decision = ScreeningDecision.UNDECIDED
    else:
        decision = ScreeningDecision.QUALIFIED

    return Evaluation(decision=decision, score=_score(outcomes), reasons=outcomes)


def _as_bool(value: Any) -> bool | None:
    """A real boolean, or the quoted tokens Hunar sometimes sends instead.

    Observed on a live screening call: fields declared `"boolean"` in
    `result_schema` came back as the strings `"true"` and `"false"`. The single
    call captured during development returned real JSON booleans, and
    `fixtures/observed_shapes.md` recorded that as settled — **one sample was not
    enough**, and the cost was every boolean criterion scoring as unknown, which
    made every candidate UNDECIDED however they answered.

    Deliberately narrow: only the two tokens the result_prompt asks for, differing
    from the documented shape by type alone. "yes", "1" and "Y" are rejected. We
    have never seen Hunar send them, and inventing a vocabulary here is exactly
    how a wrong answer gets scored as a right one — the failure this module was
    written to avoid.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _assess(criterion: Criterion, values: dict[str, Any]) -> CriterionOutcome:
    present = criterion.key in values and values[criterion.key] is not None
    value = values.get(criterion.key)

    if not present:
        # Absent is "not asked", not "answered no". The agent may simply not have
        # reached this question, and the result_prompt tells it to omit rather
        # than guess.
        return _outcome(criterion, value, "unknown", "not answered on the call")

    if criterion.type == "boolean":
        answer = _as_bool(value)
        if answer is None:
            return _outcome(criterion, value, "unknown", f"expected true or false, got {value!r}")
        # The coerced boolean, not the raw string, so the screen shows `true`
        # rather than `'true'` and the two shapes are indistinguishable downstream.
        if answer == criterion.expected:
            return _outcome(criterion, answer, "pass", f"answered {answer}")
        return _outcome(criterion, answer, "fail", f"answered {answer}, needed {criterion.expected}")

    text = str(value).strip()
    if not text:
        return _outcome(criterion, value, "unknown", "answered with nothing")
    if criterion.expected is None:
        # No specific answer required, so any real answer counts. The value is
        # still carried in the outcome for a human to read.
        return _outcome(criterion, text, "pass", f"answered '{text}'")
    if text.casefold() == str(criterion.expected).casefold():
        return _outcome(criterion, text, "pass", f"answered '{text}'")
    return _outcome(criterion, text, "fail", f"answered '{text}', needed '{criterion.expected}'")


def _outcome(criterion: Criterion, value: Any, status: Status, reason: str) -> CriterionOutcome:
    return CriterionOutcome(
        key=criterion.key,
        question=criterion.question,
        knockout=criterion.knockout,
        weight=criterion.weight,
        value=value,
        status=status,
        reason=reason,
    )


def _score(outcomes: list[CriterionOutcome]) -> float:
    """Weighted percentage over the non-knockout criteria.

    Unknowns stay in the denominator and earn nothing. Dropping them would score
    a candidate who answered one question out of five at 100%, which reads as a
    strong candidate rather than a short call. Someone we know less about should
    rank lower, and the per-criterion reasons say why.
    """
    scored = [o for o in outcomes if not o.knockout and o.weight > 0]
    total = sum(o.weight for o in scored)
    if not total:
        return 0.0
    earned = sum(o.weight for o in scored if o.status == "pass")
    return round(earned / total * 100, 1)
