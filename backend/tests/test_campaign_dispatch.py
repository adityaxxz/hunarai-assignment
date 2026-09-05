"""Campaign dispatch. The endpoint that spends money, so most of these are about
refusing to spend it.
"""

import hashlib
import io
import json
from datetime import datetime

import pytest
from sqlalchemy import select

from app.integrations.hunar.errors import HunarQuotaError
from app.models import Call, CallEvent, Campaign, CampaignStatus, Candidate
from app.services.campaign import (
    next_dial_start,
    validate_guardrails,
    validate_retry_config,
)

REQUISITION = {
    "title": "Delivery Rider",
    "location": "Bengaluru",
    "language": "ENGLISH",
    "voice_persona": "NEHA",
    "criteria": [
        {"key": "has_licence", "question": "Do you have a licence?", "type": "boolean",
         "knockout": True, "weight": 0, "expected": True}
    ],
    "candidate_variables": ["city"],
}

GOOD_GUARDRAILS = {
    "allowed_days": ["MON", "TUE", "WED", "THU", "FRI"],
    "earliest_call_time": "09:00",
    "last_call_time": "18:00",
}


async def setup_requisition(client, *, candidates: list[tuple[str, str, str]]) -> int:
    requisition_id = (await client.post("/requisitions", json=REQUISITION)).json()["id"]
    assert (await client.post(f"/requisitions/{requisition_id}/agent")).status_code == 201
    rows = "Name,Mobile,city\n" + "".join(f"{n},{p},{c}\n" for n, p, c in candidates)
    response = await client.post(
        f"/requisitions/{requisition_id}/candidates/import",
        files={"file": ("c.csv", io.BytesIO(rows.encode()), "text/csv")},
        data={"mapping": json.dumps({"name": "Name", "phone": "Mobile", "city": "city"})},
    )
    assert response.status_code == 200, response.text
    return requisition_id


# --- guardrails ------------------------------------------------------------


def test_earliest_before_0800_is_rejected_naming_the_floor() -> None:
    problems = validate_guardrails({**GOOD_GUARDRAILS, "earliest_call_time": "07:30"})

    assert any("08:00" in p for p in problems)
    assert any("from_phone_number" in p for p in problems), (
        "must say the exemption is unavailable to us, since GET /numbers/ returns none"
    )


def test_a_two_hour_window_is_rejected() -> None:
    problems = validate_guardrails(
        {**GOOD_GUARDRAILS, "earliest_call_time": "09:00", "last_call_time": "11:00"}
    )

    assert any("at least 3 hours" in p for p in problems)


def test_two_allowed_days_is_rejected() -> None:
    problems = validate_guardrails({**GOOD_GUARDRAILS, "allowed_days": ["MON", "TUE"]})

    assert any("at least 3 distinct days" in p for p in problems)


def test_every_guardrail_problem_is_reported_not_just_the_first() -> None:
    problems = validate_guardrails(
        {"allowed_days": ["MON", "MON"], "earliest_call_time": "07:00", "last_call_time": "08:00"}
    )

    assert len(problems) >= 3, problems


def test_partial_guardrails_are_rejected_as_all_or_nothing() -> None:
    problems = validate_guardrails({"allowed_days": ["MON", "TUE", "WED"],
                                    "earliest_call_time": "09:00", "last_call_time": ""})

    assert any("all-or-nothing" in p for p in problems)


def test_no_guardrails_at_all_is_fine() -> None:
    assert validate_guardrails(None) == []


# --- retry config ----------------------------------------------------------


def test_partial_retry_config_is_rejected_and_both_zero_is_accepted() -> None:
    partial = validate_retry_config({"max_retry_count": 2})
    disabled = validate_retry_config({"max_retry_count": 0, "retry_interval_hours": 0})

    assert any("all-or-nothing" in p for p in partial)
    assert disabled == [], "0/0 is how you switch retries off, not an error"


@pytest.mark.parametrize("interval", [1, 2, 5, 7, 25])
def test_retry_interval_must_be_one_of_the_allowed_values(interval: int) -> None:
    problems = validate_retry_config(
        {"max_retry_count": 1, "retry_interval_hours": interval}
    )

    assert any("retry_interval_hours must be one of" in p for p in problems)


def test_retry_count_above_ten_is_rejected() -> None:
    problems = validate_retry_config({"max_retry_count": 11, "retry_interval_hours": 3})

    assert any("between 0 and 10" in p for p in problems)


# --- dial window -----------------------------------------------------------


def test_outside_the_window_reports_when_dialling_will_actually_start() -> None:
    """A call outside the window is SCHEDULED, not rejected. A recruiter who sees
    'launched' and hears nothing will assume it is broken."""
    from zoneinfo import ZoneInfo

    friday_evening = datetime(2026, 1, 2, 22, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    window = next_dial_start(GOOD_GUARDRAILS, "Asia/Kolkata", now=friday_evening)

    assert window.dialling_now is False
    assert window.starts_at is not None
    assert window.starts_at.hour == 9
    assert window.starts_at.weekday() == 0, "next allowed day is Monday, not the weekend"
    assert "rather than reject them" in window.explanation
    assert "Monday 05 Jan at 09:00" in window.explanation


def test_inside_the_window_says_dialling_starts_now() -> None:
    from zoneinfo import ZoneInfo

    window = next_dial_start(
        GOOD_GUARDRAILS, "Asia/Kolkata",
        now=datetime(2026, 1, 5, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
    )

    assert window.dialling_now is True


# --- dispatch --------------------------------------------------------------


async def test_a_candidate_missing_a_variable_blocks_the_whole_launch(
    client, session
) -> None:
    """Refuse rather than dispatch and collect 422s one row at a time, after the
    campaign has started and the recruiter has stopped watching."""
    requisition_id = await setup_requisition(
        client, candidates=[("Asha", "9876543210", "Bengaluru")]
    )
    session.add(
        Candidate(
            requisition_id=requisition_id, name="Legacy", phone_e164="+919876543299",
            source="MANUAL", custom_fields={}, dedupe_key="legacy",
        )
    )
    await session.commit()

    response = await client.post(
        "/campaigns",
        json={"requisition_id": requisition_id, "name": "Week 1", "guardrails": GOOD_GUARDRAILS},
    )

    assert response.status_code == 422
    blocked = response.json()["detail"]["candidates"]
    assert blocked[0]["name"] == "Legacy"
    assert blocked[0]["missing"] == ["city"]
    assert (await session.execute(select(Campaign))).scalars().all() == []


async def test_call_rows_exist_before_dispatch_and_get_their_ids_after(
    client, session
) -> None:
    """The ordering that makes an early webhook recoverable and a mid-dispatch
    crash survivable."""
    requisition_id = await setup_requisition(
        client,
        candidates=[("Asha", "9876543210", "Bengaluru"), ("Bilal", "9876543211", "Bengaluru")],
    )

    response = await client.post(
        "/campaigns",
        json={"requisition_id": requisition_id, "name": "Week 1", "guardrails": GOOD_GUARDRAILS},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == CampaignStatus.RUNNING
    assert body["request_id"] == f"ARFDE-{body['id']}"
    assert body["total_calls"] == 2

    calls = (await session.execute(select(Call))).scalars().all()
    assert len(calls) == 2
    assert all(c.hunar_call_id for c in calls), "ids written back after dispatch"
    assert all(c.dispatch_error is None for c in calls)


async def test_a_webhook_that_beats_the_write_back_is_applied(client, session) -> None:
    """The reason rows are written first: an event stored unlinked is picked up
    once the id lands."""
    requisition_id = await setup_requisition(
        client, candidates=[("Asha", "9876543210", "Bengaluru")]
    )

    # A webhook arrives for a call id we have not recorded yet.
    early_id = "beat-us-to-it"
    body = json.dumps({"call_id": early_id, "status": "RINGING", "retry_count": 0})
    session.add(
        CallEvent(
            hunar_call_id=early_id, event_type="call_status_updated", raw_body=body,
            payload_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
    )
    await session.commit()

    created = await client.post(
        "/campaigns",
        json={"requisition_id": requisition_id, "name": "Week 1", "guardrails": GOOD_GUARDRAILS},
    )
    assert created.status_code == 201
    call = (await session.execute(select(Call))).scalar_one()

    # Now replay the same situation for the id we actually got, which is what
    # resolve_orphan_events handles at the end of dispatch.
    late_body = json.dumps(
        {"call_id": call.hunar_call_id, "status": "RINGING", "retry_count": 0}
    )
    session.add(
        CallEvent(
            hunar_call_id=call.hunar_call_id, event_type="call_status_updated",
            raw_body=late_body,
            payload_hash=hashlib.sha256(late_body.encode()).hexdigest(),
        )
    )
    await session.commit()

    from app.services.call_state import resolve_orphan_events

    applied = await resolve_orphan_events(session, call.hunar_call_id)
    await session.refresh(call)

    assert applied == 1
    assert call.status == "RINGING"


async def test_adopt_binds_on_phone_number_not_on_row_order(client, session) -> None:
    """The bug this replaces: binding to 'the first row missing an id' is a coin
    flip with two rows, and losing it attributes one candidate's screening result
    to another person."""
    from app.integrations.hunar.types import Call as ApiCall
    from app.services.reconcile import _adopt

    requisition_id = await setup_requisition(
        client,
        candidates=[("Asha", "9876543210", "Bengaluru"), ("Bilal", "9876543211", "Bengaluru")],
    )
    created = await client.post(
        "/campaigns",
        json={"requisition_id": requisition_id, "name": "Week 1", "guardrails": GOOD_GUARDRAILS},
    )
    campaign_id = created.json()["id"]

    # Both rows lose their ids, so ordering cannot disambiguate them.
    calls = (await session.execute(select(Call))).scalars().all()
    for call in calls:
        call.hunar_call_id = None
    await session.commit()

    bound = await _adopt(
        session, campaign_id,
        ApiCall(id="hunar-for-bilal", mobile_number="+919876543211", status="COMPLETED"),
    )
    await session.commit()

    assert bound is True
    rows = (
        await session.execute(
            select(Call, Candidate).join(Candidate, Call.candidate_id == Candidate.id)
        )
    ).all()
    bilal = next(c for c, cand in rows if cand.phone_e164 == "+919876543211")
    asha = next(c for c, cand in rows if cand.phone_e164 == "+919876543210")
    assert bilal.hunar_call_id == "hunar-for-bilal"
    assert asha.hunar_call_id is None, "the other candidate must not be touched"


async def test_a_number_hunar_does_not_accept_leaves_per_call_truth(
    client, session, monkeypatch
) -> None:
    """A campaign claiming to be running when half its calls never left is worse
    than one reporting a partial failure."""
    import app.integrations.hunar.provider as provider_module

    requisition_id = await setup_requisition(
        client,
        candidates=[("Asha", "9876543210", "Bengaluru"), ("Bilal", "9876543211", "Bengaluru")],
    )

    real = provider_module._provider
    original = real.create_bulk_calls

    async def accept_only_the_first(payload):
        accepted = await original(payload)
        return accepted[:1]  # Hunar silently drops the rest

    monkeypatch.setattr(real, "create_bulk_calls", accept_only_the_first)

    response = await client.post(
        "/campaigns",
        json={"requisition_id": requisition_id, "name": "Week 1", "guardrails": GOOD_GUARDRAILS},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == CampaignStatus.PARTIALLY_DISPATCHED
    assert "1 of 2" in body["dispatch_error"]
    assert body["funnel"]["dispatch_failed"] == 1

    calls = (await session.execute(select(Call))).scalars().all()
    dispatched = [c for c in calls if c.hunar_call_id]
    failed = [c for c in calls if c.dispatch_error]
    assert len(dispatched) == 1 and len(failed) == 1
    assert "did not accept" in failed[0].dispatch_error


async def test_a_total_dispatch_failure_marks_every_call(client, session, monkeypatch) -> None:
    import app.integrations.hunar.provider as provider_module

    requisition_id = await setup_requisition(
        client, candidates=[("Asha", "9876543210", "Bengaluru")]
    )

    async def out_of_minutes(payload):
        raise HunarQuotaError("Subscription expired", status_code=402)

    monkeypatch.setattr(provider_module._provider, "create_bulk_calls", out_of_minutes)

    response = await client.post(
        "/campaigns",
        json={"requisition_id": requisition_id, "name": "Week 1", "guardrails": GOOD_GUARDRAILS},
    )

    assert response.status_code == 201
    assert response.json()["status"] == CampaignStatus.FAILED
    call = (await session.execute(select(Call))).scalar_one()
    assert "Subscription expired" in call.dispatch_error
    assert call.hunar_call_id is None


async def test_bad_guardrails_are_rejected_before_anything_is_written(
    client, session
) -> None:
    requisition_id = await setup_requisition(
        client, candidates=[("Asha", "9876543210", "Bengaluru")]
    )

    response = await client.post(
        "/campaigns",
        json={
            "requisition_id": requisition_id,
            "name": "Too early",
            "guardrails": {**GOOD_GUARDRAILS, "earliest_call_time": "07:30"},
        },
    )

    assert response.status_code == 422
    assert any("08:00" in p for p in response.json()["detail"]["problems"])
    assert (await session.execute(select(Campaign))).scalars().all() == []
    assert (await session.execute(select(Call))).scalars().all() == []


async def test_campaign_detail_reports_the_funnel_and_an_honest_estimate(client) -> None:
    requisition_id = await setup_requisition(
        client, candidates=[("Asha", "9876543210", "Bengaluru")]
    )
    campaign_id = (
        await client.post(
            "/campaigns",
            json={"requisition_id": requisition_id, "name": "Week 1",
                  "guardrails": GOOD_GUARDRAILS,
                  "retry_config": {"max_retry_count": 2, "retry_interval_hours": 3}},
        )
    ).json()["id"]

    body = (await client.get(f"/campaigns/{campaign_id}")).json()

    assert sum(body["funnel"].values()) == body["total_calls"]
    estimate = body["estimate"]
    assert estimate["calls_to_place"] == 1
    assert estimate["worst_case_attempts"] == 3, "1 call plus 2 retries"
    assert "monetary cost" in " ".join(estimate["unknown"]), "must not invent a rate"
    assert any("single call captured" in a for a in estimate["assumptions"])


# --- the list --------------------------------------------------------------


async def test_the_list_reports_stage_counts_per_campaign(client, session) -> None:
    """The counts are what make the list usable: without them every card looks
    the same and a reviewer has to open each one to find the interesting batch."""
    requisition_id = await setup_requisition(
        client, candidates=[("Asha", "9876543210", "Bengaluru"),
                            ("Bilal", "9876543211", "Bengaluru")]
    )
    created = await client.post(
        "/campaigns", json={"requisition_id": requisition_id, "name": "Batch one"}
    )
    campaign_id = created.json()["id"]
    calls = (await session.execute(select(Call).where(Call.campaign_id == campaign_id))).scalars().all()
    calls[0].status = "COMPLETED"
    calls[0].lifecycle_status = "COMPLETED"
    calls[0].engagement_status = "ENGAGED"
    await session.commit()

    body = (await client.get("/campaigns")).json()

    row = next(c for c in body["results"] if c["id"] == campaign_id)
    assert body["total"] == 1
    assert row["name"] == "Batch one"
    assert row["kind"] == "SCREENING"
    assert row["requisition_title"] == "Delivery Rider"
    assert row["total_calls"] == 2
    assert row["funnel"]["engaged"] == 1
    assert row["funnel"]["queued"] == 1


async def test_the_list_can_be_filtered_to_sourcing(client) -> None:
    requisition_id = await setup_requisition(
        client, candidates=[("Asha", "9876543210", "Bengaluru")]
    )
    await client.post("/campaigns", json={"requisition_id": requisition_id, "name": "Screening"})
    await client.post(
        "/campaigns",
        json={"requisition_id": requisition_id, "name": "Reachout", "kind": "SOURCING"},
    )

    sourcing = (await client.get("/campaigns?kind=SOURCING")).json()

    assert sourcing["total"] == 1
    assert sourcing["results"][0]["name"] == "Reachout"


async def test_an_empty_list_is_an_empty_page_not_an_error(client) -> None:
    body = (await client.get("/campaigns")).json()

    assert body["total"] == 0
    assert body["results"] == []
