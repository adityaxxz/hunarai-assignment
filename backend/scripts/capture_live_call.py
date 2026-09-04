"""Place exactly one real Hunar call and capture every raw payload it produces.

    cd backend && uv run python -m scripts.capture_live_call \
        --mobile +91XXXXXXXXXX --name "Your Name" --tunnel https://xxx.trycloudflare.com

THIS SPENDS REAL CALLING MINUTES. Development tool. Never imported by the app,
never run by tests.

Everything is dumped as the raw JSON Hunar returns, deliberately not parsed
through types.py: the point is to find out where our models are wrong, and a
model that rejects the payload would hide exactly the evidence we came for.

Retries are pinned to 0/0 so a missed call cannot silently redial and spend more
minutes than the one call we asked for.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

RAW_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "raw"
AGENT_NAME_PREFIX = "ARFDE-CAPTURE-"
POLL_SECONDS = 5
POLL_CAP_SECONDS = 300
TERMINAL_LIFECYCLE = {"COMPLETED", "FAILED", "CANCELLED", "NOT_CONNECTED"}

# IST is a fixed +05:30 with no DST, so this avoids depending on tzdata, which
# Windows does not ship.
IST = timezone(timedelta(hours=5, minutes=30))

# Observed from a live 400, not from the docs: the org's guardrail policy floor.
# We have no outbound number of our own, so from_phone_number is unavailable and
# our guardrails cannot be looser than this.
ORG_EARLIEST_FLOOR_MINUTES = 8 * 60

# Field names that mean "your guardrails were rejected", so the fallback fires on
# the real message ("Minimum allowed earliest_call_time is 08:00.") and not just
# on the literal word "guardrails".
GUARDRAIL_HINTS = ("guardrail", "earliest_call_time", "last_call_time", "allowed_days")


def mask(phone: str) -> str:
    return f"{'*' * max(len(phone) - 4, 0)}{phone[-4:]}"


def dump(name: str, payload: Any) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"    -> {path.name}")


def now_window() -> tuple[str, str]:
    """A >=3h calling window that contains the current IST time.

    Clamped at both ends. Below by the org policy floor, which the API told us
    about the hard way:

        400 {"message": "Minimum allowed earliest_call_time is 08:00."}

    Above so a late-evening run does not produce a window crossing midnight,
    which Hunar rejects because earliest must be strictly before last.
    """
    now = datetime.now(IST)
    minutes = now.hour * 60 + now.minute
    earliest = min(max(minutes - 60, ORG_EARLIEST_FLOOR_MINUTES), 24 * 60 - 1 - 180)
    latest = earliest + 180
    return f"{earliest // 60:02d}:{earliest % 60:02d}", f"{latest // 60:02d}:{latest % 60:02d}"


def window_contains_now() -> bool:
    earliest, latest = now_window()
    now = datetime.now(IST)
    minutes = now.hour * 60 + now.minute
    to_minutes = lambda hhmm: int(hhmm[:2]) * 60 + int(hhmm[3:])  # noqa: E731
    return to_minutes(earliest) <= minutes <= to_minutes(latest)


def agent_body(stamp: str) -> dict[str, Any]:
    return {
        "name": f"{AGENT_NAME_PREFIX}{stamp}",
        "language": "ENGLISH",
        "voice_persona": "NEHA",
        "persona_name": "Neha",
        "agent_prompt": (
            "You are a hiring screener making a short courtesy call. Keep the whole "
            "call under 30 seconds. Confirm you are speaking to the right person, ask "
            "whether they are currently open to new work, ask how soon they could "
            "start, thank them and end the call. Do not ask anything else."
        ),
        "objective": "Confirm the candidate is reachable and briefly gauge availability.",
        "introduction": "Hello, am I speaking with {callee_name}?",
        "result_prompt": (
            "From the conversation, decide whether the person confirmed their "
            "identity, whether they said they are open to new work, and how soon "
            "they said they could start."
        ),
        # Flat map, which is the shape the docs example actually uses, not JSON Schema.
        "result_schema": {
            "identity_confirmed": "boolean",
            "open_to_work": "boolean",
            "availability": "string",
        },
    }


def call_body(args: argparse.Namespace, agent_id: str, stamp: str, guardrails: bool) -> dict[str, Any]:
    earliest, latest = now_window()
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "callee_name": args.name,
        "mobile_number": args.mobile,
        "request_id": f"{AGENT_NAME_PREFIX}{stamp}",
        # Both fields, both zero: Hunar rejects a partial object, and this is how
        # you say "do not redial".
        "retry_config": {"max_retry_count": 0, "retry_interval_hours": 0},
        "timezone": "Asia/Kolkata",
        "callback_config": {
            "call_status_callback_url": f"{args.tunnel}/hunar/status",
            "call_recording_callback_url": f"{args.tunnel}/hunar/recording",
            "call_result_callback_url": f"{args.tunnel}/hunar/result",
            "call_summary_callback_url": f"{args.tunnel}/hunar/summary",
        },
    }
    if guardrails:
        body["guardrails"] = {
            "allowed_days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
            "earliest_call_time": earliest,
            "last_call_time": latest,
        }
    return body


class Failed(Exception):
    pass


async def send(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    response = await client.request(method, path, **kwargs)
    if response.status_code == 402:
        print("\n" + "!" * 72)
        print("!!  402 PAYMENT REQUIRED — the account is out of calling minutes,")
        print("!!  or the subscription has expired. No call was placed.")
        print("!" * 72)
        raise SystemExit(2)
    if response.status_code >= 400:
        raise Failed(f"{method} {path} -> {response.status_code}\n{response.text[:1500]}")
    return response.json()


async def run(args: argparse.Namespace) -> int:
    if not settings.hunar_api_key:
        print("HUNAR_API_KEY is not set in backend/.env.")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    client = httpx.AsyncClient(
        base_url=settings.hunar_base_url.rstrip("/") + "/",
        headers={"X-API-Key": settings.hunar_api_key},
        timeout=httpx.Timeout(30.0, connect=5.0),
    )

    try:
        # Checked before anything is created: a call placed outside the org's
        # allowed window is not rejected, it is SCHEDULED. It would then dial
        # hours later with the cloudflared tunnel long dead, spending minutes and
        # capturing nothing, which is the worst outcome available here.
        if not window_contains_now() and not args.force:
            now = datetime.now(IST)
            earliest, latest = now_window()
            print("\n" + "!" * 72)
            print(f"!!  It is {now:%H:%M} IST. The org's earliest allowed call time is 08:00,")
            print(f"!!  so the usable window today is {earliest}-{latest} and now is outside it.")
            print("!!")
            print("!!  Hunar would accept this call and SCHEDULE it, not dial it. It would")
            print("!!  ring later with your tunnel down: minutes spent, nothing captured.")
            print("!!")
            print("!!  Run this again after 08:00 IST. Use --force to override.")
            print("!" * 72)
            return 1

        if args.agent_id:
            agent_id = args.agent_id
            print(f"[1/4] Reusing agent {agent_id} (skipping create)")
        else:
            print(f"[1/4] Creating agent {AGENT_NAME_PREFIX}{stamp} ...")
            agent = await send(client, "POST", "agents/", json=agent_body(stamp))
            agent_id = agent["id"]
            dump("01-agent-create", agent)
            print(f"    agent_id = {agent_id}")

        print("\n[2/4] Reading agent detail (for the computed *_variables) ...")
        detail = await send(client, "GET", f"agents/{agent_id}/")
        dump("02-agent-detail", detail)
        print(f"    custom_variables   = {detail.get('custom_variables')}")
        print(f"    required_variables = {detail.get('required_variables')}")
        print(f"    result_variables   = {detail.get('result_variables')}")

        earliest, latest = now_window()
        print(f"\n[3/4] Placing ONE call to {mask(args.mobile)} ({args.name})")
        print(f"    guardrail window {earliest}-{latest} IST, all 7 days, retries off")
        print(f"    callbacks -> {args.tunnel}/hunar/{{status,recording,result,summary}}")
        if not args.yes:
            if input("\n    This spends real minutes. Type YES to place it: ").strip() != "YES":
                print("    Aborted, no call placed.")
                return 1

        try:
            call = await send(client, "POST", "calls/", json=call_body(args, agent_id, stamp, True))
        except Failed as exc:
            # The org has no outbound numbers, so from_phone_number is unavailable
            # and our guardrails must sit inside the org default. If they do not,
            # fall back to omitting them entirely, which makes the org default apply.
            if not any(hint in str(exc).lower() for hint in GUARDRAIL_HINTS):
                raise
            print(f"\n    Guardrails rejected, retrying with the org default:\n    {exc}\n")
            call = await send(client, "POST", "calls/", json=call_body(args, agent_id, stamp, False))

        call_id = call["id"]
        dump("03-call-create", call)
        print(f"    call_id = {call_id}")

        print(f"\n[4/4] Polling every {POLL_SECONDS}s (cap {POLL_CAP_SECONDS}s). Ctrl+C to stop early.")
        print(f"    {'poll':>4}  {'status':<14} {'lifecycle':<14} {'engaged':<14} {'answered':<9} retries")
        deadline = asyncio.get_event_loop().time() + POLL_CAP_SECONDS
        poll = 0
        while True:
            poll += 1
            current = await send(client, "GET", f"calls/{call_id}/")
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            (RAW_DIR / f"poll-{poll:03d}.json").write_text(
                json.dumps(current, indent=2), encoding="utf-8"
            )
            lifecycle = current.get("lifecycle_status")
            print(
                f"    {poll:>4}  {str(current.get('status')):<14} {str(lifecycle):<14} "
                f"{str(current.get('engagement_status')):<14} "
                f"{str(current.get('answered_by')):<9} {current.get('retry_count')}"
            )
            if lifecycle in TERMINAL_LIFECYCLE:
                print(f"\n    Terminal lifecycle_status: {lifecycle}")
                break
            if asyncio.get_event_loop().time() >= deadline:
                print(f"\n    Poll cap reached, last lifecycle_status: {lifecycle}")
                break
            await asyncio.sleep(POLL_SECONDS)

        print(f"\nDone. {poll} polls captured in {RAW_DIR}")
        print("Leave the webhook catcher running: summary and result callbacks can lag.")
        return 0

    except Failed as exc:
        print(f"\nRequest failed:\n{exc}")
        return 1
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Place one real Hunar call and capture everything.")
    parser.add_argument("--mobile", required=True, help="E.164 number to call, e.g. +919000000000")
    parser.add_argument("--name", required=True, help="callee_name to greet")
    parser.add_argument("--tunnel", required=True, help="public https base URL of the webhook catcher")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument(
        "--agent-id",
        help="reuse an existing ARFDE-CAPTURE agent instead of creating another one",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="place the call even if now is outside the org's allowed window",
    )
    args = parser.parse_args()
    args.tunnel = args.tunnel.rstrip("/")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
