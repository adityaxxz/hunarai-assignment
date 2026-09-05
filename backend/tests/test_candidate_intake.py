"""Candidate intake. Plumbing, but the kind whose bugs dial the wrong people."""

import io
import json

import pytest
from sqlalchemy import select

from app.models import Candidate, CandidateSource, CandidateStatus, DncEntry
from app.services.candidate_intake import normalise_phone, propose_mapping

REQUISITION = {
    "title": "Delivery Rider",
    "location": "Bengaluru",
    "language": "KANNADA",
    "voice_persona": "NEHA",
    "criteria": [
        {
            "key": "has_licence",
            "question": "Do you have a valid driving licence?",
            "type": "boolean",
            "knockout": True,
            "weight": 0,
            "expected": True,
        }
    ],
    "candidate_variables": ["applied_role", "city"],
}


async def make_requisition(client) -> int:
    response = await client.post("/requisitions", json=REQUISITION)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def csv_file(text: str) -> dict:
    return {"file": ("candidates.csv", io.BytesIO(text.encode()), "text/csv")}


DEFAULT_MAPPING = {
    "name": "Name",
    "phone": "Mobile",
    "applied_role": "applied_role",
    "city": "city",
}


async def import_csv(client, requisition_id: int, text: str, mapping=None) -> dict:
    response = await client.post(
        f"/requisitions/{requisition_id}/candidates/import",
        files=csv_file(text),
        data={"mapping": json.dumps(mapping or DEFAULT_MAPPING)},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- phone normalisation ---------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "09876543210",
        "+919876543210",
        "+91 98765 43210",
        "919876543210",
        "0091 9876543210",
        " 98765-43210 ",
    ],
)
def test_indian_numbers_in_any_format_normalise_to_one_value(raw: str) -> None:
    assert normalise_phone(raw) == "+919876543210"


@pytest.mark.parametrize(
    "raw", ["", "abcd", "12345", "1234567890", "5876543210", None]
)
def test_unusable_numbers_are_rejected_rather_than_guessed(raw) -> None:
    """1234567890 and 5876543210 are ten digits but not Indian mobile ranges, so
    the +91 assumption must not be applied to them."""
    assert normalise_phone(raw) is None


# --- mapping ---------------------------------------------------------------


def test_mapping_detects_obvious_headers_and_leaves_ambiguity_unmapped() -> None:
    headers = ["Full Name", "Mobile Number", "city", "Preferred Slot"]

    mapping = propose_mapping(headers, ["applied_role", "city"])

    assert mapping["name"] == "Full Name"
    assert mapping["phone"] == "Mobile Number"
    assert mapping["city"] == "city"
    # No column plausibly means applied_role, so we ask rather than guess. A wrong
    # guess here dials people about the wrong job.
    assert mapping["applied_role"] is None


# --- import ----------------------------------------------------------------


async def test_row_missing_a_declared_variable_is_rejected_with_the_reason(client) -> None:
    """Hunar 422s a call whose custom_data lacks a declared key, so this has to
    fail at import rather than at dispatch."""
    requisition_id = await make_requisition(client)

    summary = await import_csv(
        client,
        requisition_id,
        "Name,Mobile,applied_role,city\n"
        "Asha,9876543210,Rider,Bengaluru\n"
        "Bilal,9876543211,Rider,\n",
    )

    assert summary["imported"] == 1
    assert summary["rejected"] == 1
    problem = next(p for p in summary["problems"] if p["row"] == 3)
    assert "missing required variable 'city'" in problem["reasons"]


async def test_all_reasons_are_collected_not_just_the_first(client) -> None:
    requisition_id = await make_requisition(client)

    summary = await import_csv(
        client, requisition_id, "Name,Mobile,applied_role,city\n,not-a-number,,\n"
    )

    reasons = summary["problems"][0]["reasons"]
    assert "no name" in reasons
    assert any("not a usable Indian mobile number" in r for r in reasons)
    assert "missing required variable 'applied_role'" in reasons
    assert "missing required variable 'city'" in reasons


async def test_duplicates_within_the_file_and_against_existing_are_both_caught(
    client, session
) -> None:
    requisition_id = await make_requisition(client)
    await import_csv(
        client,
        requisition_id,
        "Name,Mobile,applied_role,city\nAsha,9876543210,Rider,Bengaluru\n",
    )

    summary = await import_csv(
        client,
        requisition_id,
        "Name,Mobile,applied_role,city\n"
        "Asha again,09876543210,Rider,Bengaluru\n"   # same person, different format
        "Chandra,9876543212,Rider,Bengaluru\n"
        "Chandra twice,+919876543212,Rider,Bengaluru\n",
    )

    assert summary["duplicates_existing"] == 1
    assert summary["duplicates_in_file"] == 1
    assert summary["imported"] == 1

    stored = (await session.execute(select(Candidate))).scalars().all()
    assert len(stored) == 2
    assert {c.phone_e164 for c in stored} == {"+919876543210", "+919876543212"}


async def test_a_dnc_number_is_imported_and_marked_not_rejected(client, session) -> None:
    """Marked rather than dropped: a recruiter needs to see that the person is on
    the list, not just find them mysteriously absent."""
    session.add(DncEntry(phone_e164="+919876543210", reason="asked not to be called"))
    await session.commit()
    requisition_id = await make_requisition(client)

    summary = await import_csv(
        client,
        requisition_id,
        "Name,Mobile,applied_role,city\nAsha,9876543210,Rider,Bengaluru\n",
    )

    assert summary["do_not_call"] == 1
    assert summary["imported"] == 1, "imported, not rejected"
    candidate = (await session.execute(select(Candidate))).scalar_one()
    assert candidate.status is CandidateStatus.DO_NOT_CALL
    assert "do-not-call" in summary["problems"][0]["reasons"][0]


async def test_upload_writes_nothing_and_returns_a_preview(client, session) -> None:
    requisition_id = await make_requisition(client)

    response = await client.post(
        f"/requisitions/{requisition_id}/candidates/upload",
        files=csv_file("Full Name,Mobile,city\nAsha,9876543210,Bengaluru\n"),
    )

    body = response.json()
    assert response.status_code == 200
    assert body["headers"] == ["Full Name", "Mobile", "city"]
    assert body["mapping"]["name"] == "Full Name"
    assert body["mapping"]["applied_role"] is None
    assert body["preview"][0]["Mobile"] == "9876543210"
    assert (await session.execute(select(Candidate))).scalars().all() == []


async def test_manual_add_uses_the_same_validation(client) -> None:
    requisition_id = await make_requisition(client)

    ok = await client.post(
        f"/requisitions/{requisition_id}/candidates",
        json={
            "name": "Asha",
            "phone": "09876543210",
            "custom_fields": {"applied_role": "Rider", "city": "Bengaluru"},
        },
    )
    bad = await client.post(
        f"/requisitions/{requisition_id}/candidates",
        json={"name": "Bilal", "phone": "9876543211", "custom_fields": {"city": "Bengaluru"}},
    )

    assert ok.status_code == 201
    assert ok.json()["phone_e164"] == "+919876543210", "normalised by the same code"
    assert ok.json()["source"] == CandidateSource.MANUAL
    assert bad.status_code == 422
    assert "missing required variable 'applied_role'" in bad.json()["detail"]["reasons"]


async def test_listing_filters_by_source_and_status(client, session) -> None:
    session.add(DncEntry(phone_e164="+919876543212", reason="opted out"))
    await session.commit()
    requisition_id = await make_requisition(client)
    await import_csv(
        client,
        requisition_id,
        "Name,Mobile,applied_role,city\n"
        "Asha,9876543210,Rider,Bengaluru\n"
        "Chandra,9876543212,Rider,Bengaluru\n",
    )
    await client.post(
        f"/requisitions/{requisition_id}/candidates",
        json={
            "name": "Manual",
            "phone": "9876543213",
            "custom_fields": {"applied_role": "Rider", "city": "Bengaluru"},
        },
    )

    everyone = (await client.get(f"/requisitions/{requisition_id}/candidates")).json()
    csv_only = (
        await client.get(f"/requisitions/{requisition_id}/candidates?source=INBOUND_CSV")
    ).json()
    dnc_only = (
        await client.get(f"/requisitions/{requisition_id}/candidates?status=DO_NOT_CALL")
    ).json()

    assert everyone["total"] == 3
    assert csv_only["total"] == 2
    assert dnc_only["total"] == 1
    assert dnc_only["results"][0]["name"] == "Chandra"


# --- preflight -------------------------------------------------------------


async def test_preflight_reports_a_candidate_with_a_missing_variable_as_not_dialable(
    client, session
) -> None:
    """The launch screen has to be honest about what will fail before money is
    spent, so a candidate Hunar would reject is counted as excluded, not dialable.
    """
    requisition_id = await make_requisition(client)
    await import_csv(
        client,
        requisition_id,
        "Name,Mobile,applied_role,city\nAsha,9876543210,Rider,Bengaluru\n",
    )
    # A candidate that got in before the requisition declared 'city'.
    session.add(
        Candidate(
            requisition_id=requisition_id, name="Legacy", phone_e164="+919876543299",
            source=CandidateSource.MANUAL, custom_fields={"applied_role": "Rider"},
            dedupe_key="legacy-key",
        )
    )
    await session.commit()

    report = (await client.get(f"/requisitions/{requisition_id}/preflight")).json()

    assert report["candidates"] == 2
    assert report["dialable"] == 1
    excluded = report["excluded"][0]
    assert excluded["name"] == "Legacy"
    assert "missing city" in excluded["reasons"][0]
    # No agent has been created, so launching would fail for that reason too.
    assert report["ready"] is False
    assert any("no agent" in b for b in report["blockers"])


async def test_preflight_is_ready_once_an_agent_exists_and_someone_is_dialable(
    client,
) -> None:
    requisition_id = await make_requisition(client)
    await import_csv(
        client,
        requisition_id,
        "Name,Mobile,applied_role,city\nAsha,9876543210,Rider,Bengaluru\n",
    )
    assert (await client.post(f"/requisitions/{requisition_id}/agent")).status_code == 201

    report = (await client.get(f"/requisitions/{requisition_id}/preflight")).json()

    assert report["ready"] is True
    assert report["blockers"] == []
    assert report["dialable"] == 1
    assert report["hunar_agent_id"]
