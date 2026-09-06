"""The candidate detail endpoint: the rubric, the override and the proxy.

Most of these are about the override, because it is the one place a human
contradicts the machine and both answers have to survive.
"""

import io
import json

import pytest
from sqlalchemy import select

from app.models import AuditLog, Call, CallEvent, ScreeningDecision
from app.services.recording import silent_wav

REQUISITION = {
    "title": "Delivery Rider",
    "location": "Bengaluru",
    "language": "ENGLISH",
    "voice_persona": "NEHA",
    "criteria": [
        {"key": "has_licence", "question": "Do you have a licence?", "type": "boolean",
         "knockout": True, "weight": 0, "expected": True},
        {"key": "own_bike", "question": "Do you have your own bike?", "type": "boolean",
         "knockout": False, "weight": 3, "expected": True},
        {"key": "shift_ok", "question": "Can you work mornings?", "type": "boolean",
         "knockout": False, "weight": 1, "expected": True},
    ],
    "candidate_variables": ["city"],
}


async def setup_call(client, session, *, result: dict | None) -> int:
    """One dispatched campaign with one call, carrying `result`."""
    requisition_id = (await client.post("/requisitions", json=REQUISITION)).json()["id"]
    assert (await client.post(f"/requisitions/{requisition_id}/agent")).status_code == 201
    assert (
        await client.post(
            f"/requisitions/{requisition_id}/candidates/import",
            files={"file": ("c.csv", io.BytesIO(b"Name,Mobile,city\nAsha,9876543210,Bengaluru\n"), "text/csv")},
            data={"mapping": json.dumps({"name": "Name", "phone": "Mobile", "city": "city"})},
        )
    ).status_code == 200

    created = await client.post(
        "/campaigns", json={"requisition_id": requisition_id, "name": "Screening"}
    )
    assert created.status_code == 201, created.text

    call = (await session.execute(select(Call))).scalars().one()
    call.result = result
    call.lifecycle_status = "COMPLETED"
    call.status = "COMPLETED"
    await session.commit()
    return call.id


# --- the rubric ------------------------------------------------------------


async def test_detail_explains_every_criterion_not_just_the_decision(client, session) -> None:
    """A bare REJECTED is not actionable. The recruiter needs to see which
    question failed and what the candidate actually said."""
    call_id = await setup_call(
        client, session, result={"has_licence": False, "own_bike": True}
    )

    body = (await client.get(f"/calls/{call_id}")).json()

    reasons = {r["key"]: r for r in body["evaluation"]["reasons"]}
    assert body["evaluation"]["decision"] == "REJECTED"
    assert reasons["has_licence"]["status"] == "fail"
    assert reasons["has_licence"]["knockout"] is True
    assert reasons["has_licence"]["value"] is False
    assert "needed True" in reasons["has_licence"]["reason"]
    # Asked and answered, versus never reached: these must not look the same.
    assert reasons["own_bike"]["status"] == "pass"
    assert reasons["shift_ok"]["status"] == "unknown"
    assert "not answered" in reasons["shift_ok"]["reason"]


async def test_a_call_with_no_result_is_undecided_not_rejected(client, session) -> None:
    call_id = await setup_call(client, session, result=None)

    body = (await client.get(f"/calls/{call_id}")).json()

    assert body["evaluation"]["decision"] == "UNDECIDED"
    assert body["effective_decision"] == "UNDECIDED"
    assert all(r["status"] == "unknown" for r in body["evaluation"]["reasons"])


# --- the override ----------------------------------------------------------


async def test_override_wins_but_the_computed_decision_stays_visible(client, session) -> None:
    """The whole point: a recruiter who spoke to the candidate can overrule the
    call, and what the machine concluded is still on screen to argue with."""
    call_id = await setup_call(client, session, result={"has_licence": False})

    response = await client.post(
        f"/calls/{call_id}/override",
        json={
            "decision": "QUALIFIED",
            "reason_code": "SPOKE_TO_CANDIDATE",
            "note": "Licence issued last week, she showed me the receipt.",
        },
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["effective_decision"] == "QUALIFIED"
    assert body["override"]["decision"] == "QUALIFIED"
    assert body["override"]["reason_label"] == "I spoke to the candidate myself"
    # Not overwritten. This is the field the audit trail exists to preserve.
    assert body["evaluation"]["decision"] == "REJECTED"


async def test_override_is_written_to_the_audit_log_with_what_it_replaced(
    client, session
) -> None:
    call_id = await setup_call(client, session, result={"has_licence": False})

    await client.post(
        f"/calls/{call_id}/override",
        json={"decision": "QUALIFIED", "reason_code": "AGENT_MISHEARD", "note": None},
    )

    entry = (await session.execute(select(AuditLog))).scalars().one()
    assert entry.entity_type == "call"
    assert entry.entity_id == call_id
    assert entry.action == "override_decision"
    assert entry.reason_code == "AGENT_MISHEARD"
    assert entry.before["computed_decision"] == "REJECTED"
    assert entry.after["override_decision"] == "QUALIFIED"


async def test_a_second_override_records_the_first_one_it_replaced(client, session) -> None:
    call_id = await setup_call(client, session, result={"has_licence": False})
    await client.post(
        f"/calls/{call_id}/override",
        json={"decision": "QUALIFIED", "reason_code": "AGENT_MISHEARD", "note": None},
    )

    await client.post(
        f"/calls/{call_id}/override",
        json={"decision": "REJECTED", "reason_code": "REQUIREMENTS_CHANGED", "note": None},
    )

    entries = (
        await session.execute(select(AuditLog).order_by(AuditLog.id))
    ).scalars().all()
    assert len(entries) == 2, "corrections append, they do not replace"
    assert entries[1].before["override_decision"] == "QUALIFIED"
    assert entries[1].after["override_decision"] == "REJECTED"


async def test_other_without_a_note_is_rejected(client, session) -> None:
    """An override with no recorded reason is the one thing an audit trail must
    not contain."""
    call_id = await setup_call(client, session, result={"has_licence": True})

    bad = await client.post(
        f"/calls/{call_id}/override",
        json={"decision": "REJECTED", "reason_code": "OTHER", "note": "   "},
    )
    good = await client.post(
        f"/calls/{call_id}/override",
        json={"decision": "REJECTED", "reason_code": "OTHER", "note": "Failed a reference check."},
    )

    assert bad.status_code == 422
    assert good.status_code == 200


async def test_an_unknown_reason_code_is_rejected(client, session) -> None:
    call_id = await setup_call(client, session, result={"has_licence": True})

    response = await client.post(
        f"/calls/{call_id}/override",
        json={"decision": "REJECTED", "reason_code": "BECAUSE_I_SAID_SO"},
    )

    assert response.status_code == 422


# --- the timeline ----------------------------------------------------------


async def test_the_timeline_includes_an_event_that_arrived_before_the_call_row(
    client, session
) -> None:
    """The orphan path, made visible. An event stored unlinked and adopted later
    still belongs on this call's timeline."""
    call_id = await setup_call(client, session, result={"has_licence": True})
    call = await session.get(Call, call_id)
    session.add(
        CallEvent(
            hunar_call_id=call.hunar_call_id,
            call_id=None,           # arrived before the row existed
            event_type="call_status_updated",
            raw_body="{}",
            payload_hash="orphan-hash-for-this-test",
        )
    )
    await session.commit()

    body = (await client.get(f"/calls/{call_id}")).json()

    orphan = [e for e in body["timeline"] if not e["linked"]]
    assert len(orphan) == 1
    assert orphan[0]["event_type"] == "call_status_updated"


async def test_the_timeline_never_returns_the_raw_body(client, session) -> None:
    """It can carry a phone number and the S3 URL, and this feeds a browser."""
    call_id = await setup_call(client, session, result={"has_licence": True})
    call = await session.get(Call, call_id)
    # Seeded rather than delivered: conftest blocks the simulator's outbound
    # webhook POSTs, so nothing reaches the receiver during a test run.
    session.add(
        CallEvent(
            hunar_call_id=call.hunar_call_id,
            call_id=call.id,
            event_type="call_summary",
            raw_body='{"recording_url": "https://bucket.s3.amazonaws.com/x.wav"}',
            payload_hash="summary-hash-for-this-test",
        )
    )
    await session.commit()

    body = (await client.get(f"/calls/{call_id}")).json()

    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["linked"] is True
    assert "raw_body" not in body["timeline"][0]
    assert "s3.amazonaws.com" not in json.dumps(body)


# --- reconcile state -------------------------------------------------------


async def test_the_reason_reconciliation_stopped_is_surfaced(client, session) -> None:
    """Without this the UI cannot tell "still settling" from "we gave up", and
    it guesses."""
    call_id = await setup_call(client, session, result=None)
    call = await session.get(Call, call_id)
    call.reconcile_stopped_reason = "not engaged, no result expected"
    await session.commit()

    detail = (await client.get(f"/calls/{call_id}")).json()
    listing = (await client.get(f"/campaigns/{call.campaign_id}/calls")).json()

    assert detail["reconcile_stopped_reason"] == "not engaged, no result expected"
    assert listing["results"][0]["reconcile_stopped_reason"] == "not engaged, no result expected"


# --- the recording proxy ---------------------------------------------------


async def test_demo_mode_serves_playable_silence_rather_than_a_dead_link(
    client, session, monkeypatch
) -> None:
    """The simulator's recording_url points at demo-recordings.invalid, so a
    player wired to it would simply be broken."""
    from app.config import settings

    monkeypatch.setattr(settings, "demo_mode", True)
    call_id = await setup_call(client, session, result={"has_licence": True})

    response = await client.get(f"/calls/{call_id}/recording")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["X-Recording-Source"] == "simulated"
    assert response.content.startswith(b"RIFF")
    assert response.content == silent_wav()


async def test_the_s3_url_is_never_returned_to_the_browser(client, session) -> None:
    """The proxy exists for exactly this. A recording of a real person's phone
    call must not be reachable by anyone who reads the page source."""
    call_id = await setup_call(client, session, result={"has_licence": True})
    call = await session.get(Call, call_id)
    call.recording_url = "https://example-bucket.s3.ap-south-1.amazonaws.com/secret.wav"
    await session.commit()

    body = (await client.get(f"/calls/{call_id}")).json()

    assert body["recording_available"] is True
    assert "s3" not in json.dumps(body)
    assert "recording_url" not in body


async def test_a_missing_recording_says_why_rather_than_500ing(
    client, session, monkeypatch
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "demo_mode", False)
    call_id = await setup_call(client, session, result={"has_licence": True})
    call = await session.get(Call, call_id)
    call.recording_url = None
    await session.commit()

    response = await client.get(f"/calls/{call_id}/recording")

    assert response.status_code == 404
    assert "still be uploading" in response.json()["detail"]


def test_the_generated_wav_is_a_valid_riff_header() -> None:
    """A player given a malformed file fails silently, which looks like a bug in
    the call rather than in the fixture."""
    audio = silent_wav(seconds=1, sample_rate=8000)

    assert audio[:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"
    assert len(audio) == 44 + 8000 * 2
    assert int.from_bytes(audio[4:8], "little") == len(audio) - 8


async def test_a_missing_call_is_404_everywhere(client) -> None:
    assert (await client.get("/calls/9999")).status_code == 404
    assert (await client.get("/calls/9999/recording")).status_code == 404
    assert (
        await client.post(
            "/calls/9999/override",
            json={"decision": "QUALIFIED", "reason_code": "AGENT_MISHEARD"},
        )
    ).status_code == 404


# --- what Hunar actually sends for a boolean -------------------------------


async def test_a_boolean_returned_as_a_quoted_string_is_still_scored(client, session) -> None:
    """The exact payload from live call 5ba9fa5a on 6 September 2026.

    `result_schema` declared these `"boolean"` and Hunar answered with the
    strings `"true"`. The development capture had returned real JSON booleans and
    observed_shapes.md recorded that as settled; one sample was not enough. The
    cost of believing it: every boolean scored unknown, so a candidate who
    answered every question correctly came out UNDECIDED with a score of zero.
    """
    call_id = await setup_call(
        client,
        session,
        result={"has_licence": "true", "own_bike": "true", "shift_ok": "false"},
    )

    body = (await client.get(f"/calls/{call_id}")).json()

    reasons = {r["key"]: r for r in body["evaluation"]["reasons"]}
    assert body["evaluation"]["decision"] == "QUALIFIED"
    assert reasons["has_licence"]["status"] == "pass"
    # Normalised to a real boolean, so the screen shows `true` not `'true'`.
    assert reasons["has_licence"]["value"] is True
    assert reasons["shift_ok"]["status"] == "fail"
    assert reasons["shift_ok"]["value"] is False


async def test_a_string_that_is_not_true_or_false_stays_unknown(client, session) -> None:
    """The coercion is narrow on purpose. "yes" is unambiguous to a human and
    guesswork to a scorer, and a wrong value is worse than a missing one."""
    call_id = await setup_call(
        client,
        session,
        result={"has_licence": "yes", "own_bike": "one week", "shift_ok": 1},
    )

    body = (await client.get(f"/calls/{call_id}")).json()

    reasons = {r["key"]: r for r in body["evaluation"]["reasons"]}
    assert body["evaluation"]["decision"] == "UNDECIDED"
    assert all(r["status"] == "unknown" for r in reasons.values())
    assert "got 'yes'" in reasons["has_licence"]["reason"]


async def test_case_and_whitespace_around_the_token_are_tolerated(client, session) -> None:
    call_id = await setup_call(
        client, session, result={"has_licence": " TRUE ", "own_bike": "False"}
    )

    body = (await client.get(f"/calls/{call_id}")).json()

    reasons = {r["key"]: r for r in body["evaluation"]["reasons"]}
    assert reasons["has_licence"]["status"] == "pass"
    assert reasons["own_bike"]["status"] == "fail"


async def test_the_proxy_passes_the_upstream_content_length_through(
    client, session, monkeypatch
) -> None:
    """Without it Starlette chunks the body, the browser cannot compute a
    duration, and the player renders as 0:00 with a dead scrub bar. Seen on the
    first real recording this proxy ever served."""
    import httpx as _httpx

    from app.config import settings
    from app.services import recording as recording_module

    monkeypatch.setattr(settings, "demo_mode", False)
    call_id = await setup_call(client, session, result={"has_licence": True})
    call = await session.get(Call, call_id)
    call.recording_url = "https://example.invalid/real.wav"
    await session.commit()

    audio = b"RIFF" + b"\x00" * 2048

    async def fake_stream(url, meta=None):
        if meta is not None:
            meta["content-length"] = str(len(audio))
        yield audio

    monkeypatch.setattr(recording_module, "stream_recording", fake_stream)
    monkeypatch.setattr("app.routers.calls.stream_recording", fake_stream)

    response = await client.get(f"/calls/{call_id}/recording")

    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(audio))
    assert "chunked" not in response.headers.get("transfer-encoding", "")
    assert response.content == audio
