"""The agent builder and the rubric.

The thing worth protecting here is that prompt, result_schema and rubric are
generated from one list and therefore cannot disagree. Most of these tests are
really asserting that they still cannot.
"""

import pytest
from sqlalchemy import select

from app.integrations.hunar.types import AgentCreate
from app.models import AgentVersion, Requisition, ScreeningDecision
from app.services.agent_builder import (
    AgentBuildError,
    build_agent_payload,
    prompt_tokens,
    validate_agent_payload,
)
from app.services.evaluation import evaluate

CRITERIA = [
    {
        "key": "has_two_wheeler",
        "question": "Do you have your own two-wheeler?",
        "type": "boolean",
        "knockout": True,
        "weight": 0,
        "expected": True,
    },
    {
        "key": "has_licence",
        "question": "Do you have a valid driving licence?",
        "type": "boolean",
        "knockout": True,
        "weight": 0,
        "expected": True,
    },
    {
        "key": "delivery_experience",
        "question": "Have you done delivery work before?",
        "type": "boolean",
        "knockout": False,
        "weight": 3,
        "expected": True,
    },
    {
        "key": "availability",
        "question": "How soon could you start?",
        "type": "string",
        "knockout": False,
        "weight": 1,
        "expected": None,
    },
]


def make_requisition(**overrides) -> Requisition:
    fields = {
        "title": "Delivery Rider",
        "location": "Bengaluru",
        "language": "KANNADA",
        "voice_persona": "NEHA",
        "shift": "Morning",
        "pay_min": 18000,
        "pay_max": 25000,
        "openings": 40,
        "criteria": CRITERIA,
        "candidate_variables": ["applied_role", "city"],
    }
    fields.update(overrides)
    return Requisition(**fields)


# --- generation ------------------------------------------------------------


def test_prompt_contains_a_token_for_every_candidate_variable() -> None:
    """Hunar derives custom_variables from {token}s in the prompt. A variable
    with no token does not exist, and sending it in custom_data returns 422."""
    requisition = make_requisition()

    payload = build_agent_payload(requisition)
    tokens = prompt_tokens(payload.agent_prompt)

    for name in requisition.candidate_variables:
        assert name in tokens, f"{name} has no token, so Hunar will not create it"


def test_result_schema_keys_exactly_match_criteria_keys() -> None:
    """The invariant the whole module exists for: no criterion the agent asks
    about goes unextracted, and no schema field is unscoreable."""
    payload = build_agent_payload(make_requisition())

    assert set(payload.result_schema) == {c["key"] for c in CRITERIA}
    assert payload.result_schema["has_two_wheeler"] == "boolean"
    assert payload.result_schema["availability"] == "string"


def test_every_criterion_question_reaches_the_prompt() -> None:
    payload = build_agent_payload(make_requisition())

    for criterion in CRITERIA:
        assert criterion["question"] in payload.agent_prompt
        assert criterion["key"] in payload.result_prompt


def test_agent_name_is_prefixed_and_within_hunar_limits() -> None:
    payload = build_agent_payload(make_requisition())

    assert payload.name.startswith("ARFDE-")
    assert 3 <= len(payload.name) <= 64


def test_reserved_variables_never_become_candidate_variables() -> None:
    """callee_name is always supplied by Hunar and never becomes a custom
    variable, so declaring it would create a token that can never be filled."""
    from app.schemas import RequisitionCreate

    with pytest.raises(ValueError, match="callee_name"):
        RequisitionCreate(
            title="Rider",
            location="Bengaluru",
            criteria=[],
            candidate_variables=["callee_name"],
        )


# --- validation ------------------------------------------------------------


def test_validation_rejects_a_candidate_variable_with_no_token() -> None:
    requisition = make_requisition()
    payload = build_agent_payload(requisition)
    # Simulate a recruiter editing the token out of the preview panel.
    edited = payload.model_copy(
        update={"agent_prompt": payload.agent_prompt.replace("{city}", "their city")}
    )

    with pytest.raises(AgentBuildError) as exc:
        validate_agent_payload(requisition, edited)

    assert "city" in str(exc.value)
    assert "applied_role" not in str(exc.value), "should name only the missing one"


def test_validation_rejects_a_criterion_missing_from_result_schema() -> None:
    requisition = make_requisition()
    payload = build_agent_payload(requisition)
    schema = dict(payload.result_schema)
    schema.pop("has_licence")
    edited = payload.model_copy(update={"result_schema": schema})

    with pytest.raises(AgentBuildError) as exc:
        validate_agent_payload(requisition, edited)

    assert "has_licence" in str(exc.value)


# --- rubric ----------------------------------------------------------------


def test_failed_knockout_disqualifies_despite_a_high_score() -> None:
    """A rider without a licence does not become hireable by answering
    everything else well."""
    result = {
        "has_two_wheeler": True,
        "has_licence": False,
        "delivery_experience": True,
        "availability": "immediately",
    }

    outcome = evaluate(result, make_requisition())

    assert outcome.decision is ScreeningDecision.REJECTED
    assert outcome.score == 100.0, "the score is still high; the knockout overrides it"
    failed = [r for r in outcome.reasons if r.status == "fail"]
    assert [r.key for r in failed] == ["has_licence"]


def test_missing_key_is_unknown_not_a_failure() -> None:
    """A 30-second call may simply not have reached a question. Treating silence
    as a 'no' is how an unqualified candidate gets an interview slot."""
    result = {"has_two_wheeler": True, "delivery_experience": True}

    outcome = evaluate(result, make_requisition())

    licence = next(r for r in outcome.reasons if r.key == "has_licence")
    assert licence.status == "unknown"
    assert "not answered" in licence.reason
    # Unknown knockout means undecided, not rejected and not qualified.
    assert outcome.decision is ScreeningDecision.UNDECIDED


def test_all_knockouts_passing_qualifies_and_scores_the_rest() -> None:
    result = {
        "has_two_wheeler": True,
        "has_licence": True,
        "delivery_experience": False,
        "availability": "two weeks",
    }

    outcome = evaluate(result, make_requisition())

    assert outcome.decision is ScreeningDecision.QUALIFIED
    # delivery_experience weight 3 failed, availability weight 1 passed.
    assert outcome.score == 25.0


def test_unknowns_stay_in_the_denominator() -> None:
    """Dropping them would score a candidate who answered one of five at 100%,
    which reads as a strong candidate rather than a short call."""
    both_known = evaluate(
        {"has_two_wheeler": True, "has_licence": True,
         "delivery_experience": True, "availability": "now"},
        make_requisition(),
    )
    one_unknown = evaluate(
        {"has_two_wheeler": True, "has_licence": True, "delivery_experience": True},
        make_requisition(),
    )

    assert both_known.score == 100.0
    assert one_unknown.score == 75.0, "the unanswered weight-1 criterion still counts"


def test_a_wrongly_typed_answer_is_unknown_rather_than_trusted() -> None:
    outcome = evaluate({"has_two_wheeler": "yes please"}, make_requisition())

    wheeler = next(r for r in outcome.reasons if r.key == "has_two_wheeler")
    assert wheeler.status == "unknown"
    assert "expected true or false" in wheeler.reason


# --- persistence -----------------------------------------------------------


async def test_creating_an_agent_stores_the_read_back_variables(client, session) -> None:
    """Not what we sent. The capture returned custom_variables: [] for an agent
    we assumed would have one, so Hunar's own answer is the only reliable source.
    """
    created = await client.post("/requisitions", json=_requisition_body())
    requisition_id = created.json()["id"]

    response = await client.post(f"/requisitions/{requisition_id}/agent")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["hunar_agent_id"]
    # The simulator derives these the same way Hunar does: from prompt tokens,
    # minus the always-required ones.
    assert set(body["custom_variables"]) == {"applied_role", "city"}
    assert "callee_name" not in body["custom_variables"]
    assert set(body["required_variables"]) == {"callee_name", "mobile_number"}
    assert set(body["result_variables"]) == {c["key"] for c in CRITERIA}


async def test_edited_payload_is_persisted_as_sent(client, session) -> None:
    created = await client.post("/requisitions", json=_requisition_body())
    requisition_id = created.json()["id"]
    preview = (await client.post(f"/requisitions/{requisition_id}/agent/preview")).json()

    preview["objective"] = "A hand-edited objective."
    preview["introduction"] = "Hello {callee_name}, quick question about the rider job."
    response = await client.post(f"/requisitions/{requisition_id}/agent", json=preview)

    assert response.status_code == 201, response.text
    assert response.json()["objective"] == "A hand-edited objective."
    assert response.json()["introduction"].startswith("Hello {callee_name}")


async def test_creating_twice_versions_and_never_mutates_the_first(client, session) -> None:
    created = await client.post("/requisitions", json=_requisition_body())
    requisition_id = created.json()["id"]

    first = (await client.post(f"/requisitions/{requisition_id}/agent")).json()
    preview = (await client.post(f"/requisitions/{requisition_id}/agent/preview")).json()
    preview["objective"] = "Second version."
    second = (
        await client.post(f"/requisitions/{requisition_id}/agent", json=preview)
    ).json()

    assert (first["version"], second["version"]) == (1, 2)
    assert first["hunar_agent_id"] != second["hunar_agent_id"]

    stored = (
        await session.execute(
            select(AgentVersion)
            .where(AgentVersion.requisition_id == requisition_id)
            .order_by(AgentVersion.version)
        )
    ).scalars().all()
    assert len(stored) == 2
    assert stored[0].objective == first["objective"], "version 1 must be untouched"
    assert stored[1].objective == "Second version."


async def test_preview_does_not_call_hunar(client, session) -> None:
    created = await client.post("/requisitions", json=_requisition_body())
    requisition_id = created.json()["id"]

    response = await client.post(f"/requisitions/{requisition_id}/agent/preview")

    assert response.status_code == 200
    assert AgentCreate.model_validate(response.json())
    versions = (await session.execute(select(AgentVersion))).scalars().all()
    assert versions == [], "a preview must not create anything"


async def test_agent_creation_rejects_a_broken_edit_with_a_named_reason(client) -> None:
    created = await client.post("/requisitions", json=_requisition_body())
    requisition_id = created.json()["id"]
    preview = (await client.post(f"/requisitions/{requisition_id}/agent/preview")).json()
    preview["agent_prompt"] = preview["agent_prompt"].replace("{city}", "their city")

    response = await client.post(f"/requisitions/{requisition_id}/agent", json=preview)

    assert response.status_code == 422
    assert "city" in response.json()["detail"]


def _requisition_body() -> dict:
    return {
        "title": "Delivery Rider",
        "location": "Bengaluru",
        "language": "KANNADA",
        "voice_persona": "NEHA",
        "shift": "Morning",
        "pay_min": 18000,
        "pay_max": 25000,
        "openings": 40,
        "criteria": CRITERIA,
        "candidate_variables": ["applied_role", "city"],
    }
