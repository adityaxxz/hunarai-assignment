"""Turn a requisition into a Hunar agent configuration.

**One definition of the screening criteria drives three things at once.** The
questions the agent speaks, the `result_schema` Hunar extracts against, and the
rubric that decides qualification are all generated from `requisition.criteria`.
They cannot drift, because there is only one of them.

That matters more than it sounds. The failure it prevents is the quiet one: a
recruiter adds a question to the prompt, forgets the schema, and from then on the
agent asks something nobody ever records — or worse, the rubric scores a key the
agent stopped asking about and every candidate silently fails it. Neither shows
up as an error. Generating all three from one list makes that state unreachable
rather than merely discouraged.

**Deterministic templating, no LLM.** A requisition is structured input: fields,
a list of criteria, a list of variables. Rendering it with a template is
reproducible, diffable and reviewable, and a recruiter editing the preview sees
exactly what will be sent. The model is reserved for Module B, where the input is
a paste-in job description and there is genuinely unstructured text to interpret.

**The custom-variable contract, learned the hard way.** Hunar DERIVES
`custom_variables` from `{placeholder}` tokens in the prompt text. There is no
field that declares them. If a token is not in the prompt the variable does not
exist, and passing it in `custom_data` returns 422 "Custom data keys are not
present". `callee_name` and `mobile_number` are always in `required_variables`
and never become custom variables — the live capture created an agent whose
introduction contained `{callee_name}` and got `custom_variables: []` back. See
`fixtures/observed_shapes.md`.
"""

import re

from app.integrations.hunar.types import AgentCreate
from app.models import Requisition, RequisitionKind
from app.schemas import Criterion

# The org account is shared and already holds 100+ agents from other people's
# testing, so ours have to be findable by name.
AGENT_NAME_PREFIX = "ARFDE-"
_MAX_AGENT_NAME = 64

# Supplied by Hunar on every call, never derived from the prompt.
RESERVED_VARIABLES = ("callee_name", "mobile_number")

_TOKEN = re.compile(r"\{(\w+)\}")


class AgentBuildError(Exception):
    """Raised with a message naming the specific missing item, so the caller can
    return it verbatim rather than a 500."""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def agent_name(requisition: Requisition) -> str:
    slug = slugify(f"{requisition.title}-{requisition.location}")
    return f"{AGENT_NAME_PREFIX}{slug}"[:_MAX_AGENT_NAME]


def prompt_tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text))


def build_agent_payload(requisition: Requisition) -> AgentCreate:
    criteria = [Criterion.model_validate(c) for c in requisition.criteria]
    return AgentCreate(
        name=agent_name(requisition),
        language=requisition.language,
        voice_persona=requisition.voice_persona,
        persona_name=None,
        agent_prompt=_agent_prompt(requisition, criteria),
        objective=_objective(requisition),
        introduction=_introduction(requisition),
        result_prompt=_result_prompt(criteria),
        result_schema=_result_schema(criteria),
    )


def _result_schema(criteria: list[Criterion]) -> dict[str, str]:
    """A flat {key: "boolean"|"string"} map, not JSON Schema.

    The docs example and the live capture both use the flat form, and the
    capture confirmed the declared type is honoured: "boolean" produced a real
    JSON boolean in the result, not the string "true".
    """
    return {c.key: c.type for c in criteria}


def _introduction(requisition: Requisition) -> str:
    # {callee_name} is a required variable, always populated by Hunar, and does
    # NOT become a custom variable.
    if requisition.kind is RequisitionKind.SOURCING:
        # Colder, and it asks. This person did not apply, is probably at work,
        # and has every right to say no before hearing anything else. Opening
        # with the pitch instead of the permission is how a sourcing call becomes
        # a complaint.
        return (
            f"Hello, am I speaking with {{callee_name}}? My name is Neha and I am "
            f"calling from a recruitment team about a {requisition.title} role in "
            f"{requisition.location}. This is not a sales call and it will take two "
            f"minutes. Is now an alright time to talk?"
        )
    return (
        f"Hello, am I speaking with {{callee_name}}? "
        f"I am calling about the {requisition.title} opening in {requisition.location}."
    )


def _objective(requisition: Requisition) -> str:
    if requisition.kind is RequisitionKind.SOURCING:
        return (
            f"Find out whether this person is open to a {requisition.title} role in "
            f"{requisition.location}, and if they are, capture what it would take. "
            f"Leave politely at the first sign they are not interested."
        )
    return (
        f"Screen the candidate for the {requisition.title} role in "
        f"{requisition.location} and record their answers to each question."
    )


def _opening_lines(requisition: Requisition) -> list[str]:
    """How the agent is told to behave. The only real difference between the two
    kinds of call, and it is entirely about who picked up."""
    if requisition.kind is RequisitionKind.SOURCING:
        return [
            f"You are a recruiter making a first approach about a {requisition.title} "
            f"role in {requisition.location}. The person you are calling did not "
            f"apply for anything. They have not heard of you.",
            "",
            "Ask permission before anything else and accept the answer. If they say "
            "it is a bad time, offer to call back and ask when, then end the call. "
            "If they say they are not looking, thank them and end the call — do not "
            "argue, do not pitch, do not ask why. Never imply they applied. Never "
            "say another company sent you. Keep the whole call under two minutes.",
            "",
        ]
    return [
        f"You are a hiring screener calling about a {requisition.title} role in "
        f"{requisition.location}.",
        "",
        "Speak simply and briefly. This is a phone call to someone who is "
        "probably outdoors, on a cheap handset, and busy. Ask one question at a "
        "time and wait for the answer. Do not read out a list. Do not explain "
        "the role unless asked. Keep the whole call under two minutes.",
        "",
    ]


def _agent_prompt(requisition: Requisition, criteria: list[Criterion]) -> str:
    lines = _opening_lines(requisition)

    details = []
    if requisition.shift:
        details.append(f"Shift: {requisition.shift}.")
    if requisition.pay_min and requisition.pay_max:
        details.append(f"Pay: {requisition.pay_min} to {requisition.pay_max} per month.")
    elif requisition.pay_min:
        details.append(f"Pay: from {requisition.pay_min} per month.")
    if details:
        lines.extend(["Answer these only if the candidate asks:", *details, ""])

    # Every candidate variable gets a {token} here. This block is the ONLY reason
    # the variables exist: Hunar derives custom_variables from these tokens, so
    # dropping the block would silently delete the contract and every dispatch
    # carrying that key would 422.
    if requisition.candidate_variables:
        lines.append("What you already know about this person:")
        for name in requisition.candidate_variables:
            lines.append(f"- {name.replace('_', ' ')}: {{{name}}}")
        lines.append("")

    lines.append("Ask these questions, in this order:")
    for index, criterion in enumerate(criteria, start=1):
        lines.append(f"{index}. {criterion.question}")
    closing = (
        "Stop asking questions the moment they say they are not interested, and "
        "thank them for their time. Otherwise ask what you can and end politely."
        if requisition.kind is RequisitionKind.SOURCING
        else "If the candidate cannot answer a question, move on rather than "
        "pressing. Thank them and end the call once you have asked all of them "
        "or it is clear they do not want to continue."
    )
    lines.extend(["", closing])
    return "\n".join(lines)


def _result_prompt(criteria: list[Criterion]) -> str:
    lines = [
        "Read the conversation and extract one value per field below. Use only "
        "what the candidate actually said. If they did not answer a question, "
        "leave that field out entirely rather than guessing — an absent field is "
        "read as 'not asked', and a wrong value is worse than a missing one.",
        "",
    ]
    for criterion in criteria:
        expectation = (
            "true or false" if criterion.type == "boolean" else "a short phrase"
        )
        lines.append(f"- {criterion.key}: {criterion.question} Answer with {expectation}.")
    return "\n".join(lines)


def validate_agent_payload(requisition: Requisition, payload: AgentCreate) -> None:
    """Check the payload against the requisition before it reaches Hunar.

    Both failures below produce a 422 from Hunar or, worse, an agent that looks
    fine and silently drops data. Catching them here means the recruiter sees
    which item is missing rather than a vendor error code.
    """
    problems: list[str] = []

    tokens = prompt_tokens(payload.agent_prompt) | prompt_tokens(payload.introduction)
    missing_tokens = [v for v in requisition.candidate_variables if v not in tokens]
    if missing_tokens:
        problems.append(
            "these candidate variables have no {token} in the prompt, so Hunar "
            "will not create them and any call sending them will be rejected: "
            + ", ".join(missing_tokens)
        )

    reserved = [v for v in RESERVED_VARIABLES if v in tokens and v in requisition.candidate_variables]
    if reserved:
        problems.append(
            f"{', '.join(reserved)} is always supplied by Hunar and must not be a candidate variable"
        )

    criteria_keys = [c["key"] for c in requisition.criteria]
    missing_schema = [k for k in criteria_keys if k not in payload.result_schema]
    if missing_schema:
        problems.append(
            "these criteria are missing from result_schema, so nothing will be "
            "extracted for them: " + ", ".join(missing_schema)
        )

    extra_schema = [k for k in payload.result_schema if k not in criteria_keys]
    if extra_schema:
        problems.append(
            "result_schema declares fields with no matching criterion, which the "
            "rubric cannot score: " + ", ".join(extra_schema)
        )

    if problems:
        raise AgentBuildError("; ".join(problems))
