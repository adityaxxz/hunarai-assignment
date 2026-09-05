"""Module B. Mostly about refusing to dial people who did not ask to be dialled."""

import httpx
import pytest
from sqlalchemy import select

from app.integrations.people_search.base import SourcingProfile
from app.integrations.people_search.fixture import FixtureProvider
from app.integrations.people_search.pdl import MAX_RESULTS, PDLProvider, PeopleSearchError
from app.models import Candidate, CandidateSource, DncEntry, Requisition, RequisitionKind
from app.services.agent_builder import build_agent_payload
from app.services.contact_resolution import resolve
from app.services.jd_to_query import jd_to_query

JD = """
We are hiring a Senior Instrumentation Engineer in Pune for a process automation
project. The role covers loop checking, commissioning support and vendor
coordination. Four or more years of plant experience is expected.
"""


async def make_search(client, jd: str = JD) -> dict:
    response = await client.post("/sourcing/searches", json={"jd_text": jd})
    assert response.status_code == 201, response.text
    return response.json()


async def run(client, search_id: int, query: dict | None = None) -> dict:
    response = await client.post(
        f"/sourcing/searches/{search_id}/run",
        json={"query": query if query is not None else {"bool": {"must": []}}, "limit": 10},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- JD to query -----------------------------------------------------------


async def test_the_fallback_produces_a_usable_query_and_admits_it_is_a_fallback() -> None:
    """Gemini being unavailable must not take the feature down, and must not
    silently look like it worked."""
    generated = await jd_to_query(JD)

    assert generated.source == "fallback", "no GEMINI_API_KEY is set in tests"
    assert "fallback" in generated.note.lower() or "without a model" in generated.note.lower()
    assert any("engineer" in t.lower() for t in generated.titles)
    assert "pune" in generated.locations
    assert generated.query["bool"]["must"], "an empty query would return the whole database"


async def test_an_empty_job_description_is_rejected(client) -> None:
    response = await client.post("/sourcing/searches", json={"jd_text": "   " * 10})

    assert response.status_code == 422


# --- the provider seam -----------------------------------------------------


async def test_the_fixture_provider_actually_filters_on_the_query() -> None:
    """The editable query control is a lie if editing it changes nothing."""
    provider = FixtureProvider()

    backend = await provider.search({"bool": {"must": [{"match": {"job_title": "backend"}}]}}, 10)
    piping = await provider.search({"bool": {"must": [{"match": {"job_title": "piping"}}]}}, 10)

    assert {p.full_name for p in backend.profiles} != {p.full_name for p in piping.profiles}
    assert all("Backend" in (p.current_title or "") for p in backend.profiles)


async def test_pdl_never_follows_scroll_token_and_caps_results() -> None:
    """One credit per record on a 100-record month. A pagination loop here does
    not cost a slow endpoint, it costs the whole month."""
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "status": 200,
                "total": 4000,
                "scroll_token": "there-is-more",
                "data": [{"full_name": f"Person {i}"} for i in range(5)],
            },
        )

    provider = PDLProvider("test-key", transport=httpx.MockTransport(handler))
    result = await provider.search({"bool": {"must": []}}, limit=500)

    assert f'"size": {MAX_RESULTS}' in seen["body"].replace('"size":', '"size": ')
    assert len(result.profiles) == 5
    assert any("4000" in note for note in result.notes), "truncation has to be visible"


async def test_pdl_quota_exhaustion_is_named_not_retried() -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(402, json={"error": {"message": "quota"}})

    provider = PDLProvider("test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(PeopleSearchError, match="quota"):
        await provider.search({}, limit=5)
    assert calls["n"] == 1, "retrying a quota error spends what is left of it"


# --- contact resolution ----------------------------------------------------


def test_a_profile_with_no_number_resolves_to_unresolved_not_an_error() -> None:
    """The normal outcome on a free tier, not an exceptional one."""
    profile = SourcingProfile(full_name="No Number", phone=None)

    resolved = resolve(profile, allow_fixture=False)

    assert resolved.dialable is False
    assert resolved.resolver == "unresolved"
    assert "current plan" in resolved.detail


def test_the_resolver_is_named_on_every_record() -> None:
    """The badge is the honest part of Module B. A recruiter about to call
    strangers has to know whether the number came from the vendor or from us."""
    real = resolve(SourcingProfile(full_name="Has One", phone="9876543210"), allow_fixture=True)
    demo = resolve(SourcingProfile(full_name="Has None"), allow_fixture=True)

    assert real.resolver == "provider"
    assert real.phone_e164 == "+919876543210"
    assert demo.resolver == "fixture"
    assert demo.phone_e164.startswith("+91")


def test_demo_numbers_are_derived_from_the_profile_not_randomly() -> None:
    """sha256, not hash(): the built-in is randomised per process, so a restart
    would hand the same profile a different number and reshuffle who is who."""
    same_person = SourcingProfile(full_name="Stable", linkedin_url="https://linkedin.invalid/in/x")
    again = SourcingProfile(full_name="Stable", linkedin_url="https://linkedin.invalid/in/x")
    different = SourcingProfile(full_name="Other", linkedin_url="https://linkedin.invalid/in/y")

    number = resolve(same_person, allow_fixture=True).phone_e164
    assert number == resolve(again, allow_fixture=True).phone_e164
    assert number != resolve(different, allow_fixture=True).phone_e164
    assert len(number) == 13 and number.startswith("+919876")


def test_a_provider_number_we_cannot_normalise_is_reported_not_dropped() -> None:
    """A gap in normalise_phone should look like a gap, not like missing data."""
    profile = SourcingProfile(full_name="Odd", phone="ext. 4471")

    resolved = resolve(profile, allow_fixture=True)

    assert resolved.dialable is False
    assert "not a usable Indian mobile number" in resolved.detail


# --- the consent gate ------------------------------------------------------


async def test_import_without_confirm_is_refused_and_says_why(client) -> None:
    search = await make_search(client)
    found = await run(client, search["id"])

    response = await client.post(
        f"/sourcing/searches/{search['id']}/import",
        json={
            "title": "Instrumentation Engineer", "location": "Pune",
            "profiles": [found["profiles"][0]], "confirm": False,
        },
    )

    assert response.status_code == 422
    assert "did not apply" in response.json()["detail"]["problems"][0]


async def test_running_a_search_writes_no_candidates(client, session) -> None:
    """Consent has not been given yet. A search that filled the table would make
    the gate decorative."""
    search = await make_search(client)

    await run(client, search["id"])

    assert (await session.execute(select(Candidate))).scalars().all() == []


async def test_import_skips_dnc_duplicates_and_the_unreachable(client, session) -> None:
    search = await make_search(client)
    found = await run(client, search["id"])
    dialable = [p for p in found["profiles"] if p["phone_e164"]]
    session.add(DncEntry(phone_e164=dialable[0]["phone_e164"], reason="asked not to be called"))
    await session.commit()

    response = await client.post(
        f"/sourcing/searches/{search['id']}/import",
        json={
            "title": "Instrumentation Engineer", "location": "Pune",
            "confirm": True,
            "profiles": [
                dialable[0],                                    # on the DNC list
                dialable[1],
                dialable[1],                                    # same number twice
                {"full_name": "Unreachable", "phone_e164": None},
            ],
        },
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["imported"] == 1
    reasons = {s["reason"] for s in body["skipped"]}
    assert "on the do-not-call list" in reasons
    assert "the same number appears twice in this selection" in reasons
    assert "no dialable number was resolved" in reasons


async def test_someone_already_a_candidate_is_not_imported_twice(client, session) -> None:
    """Across pipelines, not just within one. The point is that the same person
    is not called by Module A and Module B in the same week."""
    search = await make_search(client)
    found = await run(client, search["id"])
    dialable = [p for p in found["profiles"] if p["phone_e164"]][0]
    session.add(
        Candidate(
            name="Already Here", phone_e164=dialable["phone_e164"],
            source=CandidateSource.INBOUND_CSV, custom_fields={}, dedupe_key="existing",
        )
    )
    await session.commit()

    response = await client.post(
        f"/sourcing/searches/{search['id']}/import",
        json={"title": "Instrumentation Engineer", "location": "Pune",
              "confirm": True, "profiles": [dialable]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["skipped"][0]["reason"].startswith("already a candidate")


async def test_imported_people_land_in_the_existing_candidates_table(client, session) -> None:
    """No second table, no second pipeline. Just a different `source`."""
    search = await make_search(client)
    found = await run(client, search["id"])
    dialable = [p for p in found["profiles"] if p["phone_e164"]][:3]

    response = await client.post(
        f"/sourcing/searches/{search['id']}/import",
        json={"title": "Instrumentation Engineer", "location": "Pune",
              "confirm": True, "profiles": dialable},
    )

    requisition_id = response.json()["requisition_id"]
    stored = (await session.execute(select(Candidate))).scalars().all()
    assert len(stored) == 3
    assert all(c.source is CandidateSource.SOURCED_PDL for c in stored)
    assert all(c.requisition_id == requisition_id for c in stored)
    assert all(c.sourcing_search_id == search["id"] for c in stored)
    assert all(c.custom_fields["contact_resolver"] for c in stored)


# --- the reachout agent ----------------------------------------------------


async def test_the_sourcing_agent_asks_permission_and_the_screening_one_does_not(
    client, session
) -> None:
    """The only real difference between the two kinds of call is who picked up."""
    search = await make_search(client)
    found = await run(client, search["id"])
    imported = await client.post(
        f"/sourcing/searches/{search['id']}/import",
        json={"title": "Instrumentation Engineer", "location": "Pune", "confirm": True,
              "profiles": [p for p in found["profiles"] if p["phone_e164"]][:1]},
    )
    sourcing = await session.get(Requisition, imported.json()["requisition_id"])
    # Column defaults are applied on flush, so an unsaved Requisition needs
    # these stated explicitly.
    screening = Requisition(
        kind=RequisitionKind.SCREENING, title="Delivery Rider", location="Bengaluru",
        language="ENGLISH", voice_persona="NEHA", criteria=[], candidate_variables=[],
    )

    cold = build_agent_payload(sourcing)
    warm = build_agent_payload(screening)

    assert sourcing.kind is RequisitionKind.SOURCING
    assert "alright time to talk" in cold.introduction
    assert "did not apply" in cold.agent_prompt
    assert "do not argue" in cold.agent_prompt.lower()
    assert "alright time to talk" not in warm.introduction
    # The five reachout fields the brief asks for, plus the objection field the
    # aggregates need.
    assert set(cold.result_schema) == {
        "open_to_move", "notice_period", "current_ctc", "expected_ctc",
        "preferred_callback_time", "reason_not_interested",
    }
    assert cold.result_schema["open_to_move"] == "boolean"


async def test_the_personalisation_tokens_are_in_the_prompt(client, session) -> None:
    """Otherwise Hunar never creates the custom variables and every call 422s."""
    search = await make_search(client)
    found = await run(client, search["id"])
    imported = await client.post(
        f"/sourcing/searches/{search['id']}/import",
        json={"title": "Instrumentation Engineer", "location": "Pune", "confirm": True,
              "profiles": [p for p in found["profiles"] if p["phone_e164"]][:1]},
    )

    payload = build_agent_payload(await session.get(Requisition, imported.json()["requisition_id"]))

    assert "{current_title}" in payload.agent_prompt
    assert "{current_company}" in payload.agent_prompt


# --- dispatch reuse and insights -------------------------------------------


async def test_sourcing_dispatches_through_the_existing_campaign_endpoint(
    client, session
) -> None:
    """If this needed its own endpoint, Module B would have grown a second call
    pipeline. It does not."""
    search = await make_search(client)
    found = await run(client, search["id"])
    requisition_id = (
        await client.post(
            f"/sourcing/searches/{search['id']}/import",
            json={"title": "Instrumentation Engineer", "location": "Pune", "confirm": True,
                  "profiles": [p for p in found["profiles"] if p["phone_e164"]][:2]},
        )
    ).json()["requisition_id"]
    assert (await client.post(f"/requisitions/{requisition_id}/agent")).status_code == 201

    created = await client.post(
        "/campaigns",
        json={"requisition_id": requisition_id, "name": "Reachout", "kind": "SOURCING"},
    )

    assert created.status_code == 201, created.text
    assert created.json()["total_calls"] == 2
    insights = await client.get(f"/sourcing/campaigns/{created.json()['id']}/insights")
    assert insights.status_code == 200
    assert insights.json()["kind"] == "SOURCING"


async def test_insights_report_interest_over_answered_calls_not_over_everyone(
    client, session
) -> None:
    """Dividing by people who never picked up measures reachability, not
    interest, and the two get confused constantly."""
    from app.models import Call

    search = await make_search(client)
    found = await run(client, search["id"])
    requisition_id = (
        await client.post(
            f"/sourcing/searches/{search['id']}/import",
            json={"title": "Instrumentation Engineer", "location": "Pune", "confirm": True,
                  "profiles": [p for p in found["profiles"] if p["phone_e164"]][:3]},
        )
    ).json()["requisition_id"]
    await client.post(f"/requisitions/{requisition_id}/agent")
    campaign_id = (
        await client.post(
            "/campaigns",
            json={"requisition_id": requisition_id, "name": "Reachout", "kind": "SOURCING"},
        )
    ).json()["id"]

    calls = (await session.execute(select(Call).where(Call.campaign_id == campaign_id))).scalars().all()
    calls[0].result = {"open_to_move": True, "notice_period": "60 days",
                       "preferred_callback_time": "after 7pm"}
    calls[1].result = {"open_to_move": False, "reason_not_interested": "just got promoted"}
    # calls[2] never answered: no result at all.
    await session.commit()

    body = (await client.get(f"/sourcing/campaigns/{campaign_id}/insights")).json()

    assert body["total_calls"] == 3
    assert body["answered"] == 2
    assert body["interested"] == 1
    assert body["interest_rate"] == 50.0, "1 of 2 answered, not 1 of 3 dialled"
    assert body["notice_period"] == {"2 months": 1}
    assert body["objections"] == {"just got promoted": 1}


async def test_interest_rate_is_null_rather_than_zero_when_nobody_answered(
    client, session
) -> None:
    """0% interest and "nobody has picked up" are different facts."""
    search = await make_search(client)
    found = await run(client, search["id"])
    requisition_id = (
        await client.post(
            f"/sourcing/searches/{search['id']}/import",
            json={"title": "Instrumentation Engineer", "location": "Pune", "confirm": True,
                  "profiles": [p for p in found["profiles"] if p["phone_e164"]][:1]},
        )
    ).json()["requisition_id"]
    await client.post(f"/requisitions/{requisition_id}/agent")
    campaign_id = (
        await client.post(
            "/campaigns",
            json={"requisition_id": requisition_id, "name": "Reachout", "kind": "SOURCING"},
        )
    ).json()["id"]

    body = (await client.get(f"/sourcing/campaigns/{campaign_id}/insights")).json()

    assert body["interest_rate"] is None
