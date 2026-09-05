"""Manual check that a live Hunar API key works. Never run by tests or on startup.

    cd backend && uv run python -m scripts.smoke_hunar

Reads HUNAR_API_KEY and HUNAR_BASE_URL from backend/.env. Only does GETs, so it
cannot place a call or spend calling minutes.
"""

import asyncio
import sys

from app.config import settings
from app.integrations.hunar.client import HunarClient
from app.integrations.hunar.errors import (
    HunarAuthError,
    HunarError,
    HunarQuotaError,
    HunarTimeoutError,
)


def mask(phone: str) -> str:
    """Org numbers are not candidate numbers, but the no-full-numbers-in-output
    rule is easier to keep if it has no exceptions."""
    return f"{'*' * max(len(phone) - 4, 0)}{phone[-4:]}" if phone else "(none)"


async def main() -> int:
    if not settings.hunar_api_key:
        print("HUNAR_API_KEY is not set in backend/.env. Nothing to check.")
        return 1

    print(f"Base URL: {settings.hunar_base_url}")
    # Deliberately bypasses get_voice_provider(): the whole point of this script
    # is to check a live key, so routing it through the demo-mode switch would
    # let it "pass" against the simulator and prove nothing. This is the only
    # place outside integrations/hunar/ that names HunarClient.
    client = HunarClient(settings.hunar_api_key, base_url=settings.hunar_base_url)
    try:
        agents = await client.list_agents(page_size=200)
        print(f"\nAgents: {agents.count}")
        for agent in agents.results[:10]:
            variables = ", ".join(agent.custom_variables) or "none"
            print(f"  {agent.id}  {agent.name}  [{agent.language}/{agent.voice_persona}]")
            print(f"      status={agent.status}  custom_variables={variables}")
        if agents.count > 10:
            print(f"  ... and {agents.count - 10} more")

        numbers = await client.list_numbers()
        print(f"\nOutbound numbers: {numbers.count}")
        for number in numbers.results:
            default = " (default)" if number.is_default else ""
            countries = ",".join(number.allowed_countries) or "-"
            print(
                f"  {mask(number.phone_number)}  {number.provider}  "
                f"allows={countries}  validated={number.is_validated}{default}"
            )

        print("\nKey works.")
        return 0

    except HunarAuthError as exc:
        print(f"\n401: the key was rejected. {exc.message}")
        print("Check HUNAR_API_KEY in backend/.env, and whether the trial key has expired.")
        return 1
    except HunarQuotaError as exc:
        print(f"\n402: the key is valid but the account cannot be used. {exc.message}")
        print("Subscription expired or calling minutes exhausted. Reads may still work; calls will not.")
        return 1
    except HunarTimeoutError as exc:
        print(f"\nCould not reach Hunar: {exc.message}")
        return 1
    except HunarError as exc:
        print(f"\nUnexpected API error: {exc}")
        if exc.details:
            print(f"details: {exc.details}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
