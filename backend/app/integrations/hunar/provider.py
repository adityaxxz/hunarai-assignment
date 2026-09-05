"""The `VoiceProvider` seam: one of the three interfaces notes.md section 6
approves, because it has two real implementations in this repo right now.

Why it exists: the Hunar trial key is revoked three days after issue, and the
deployed link has to stay demonstrable after that. It is also the only honest way
to demo a voice product — you cannot dial live prospects in a review meeting.

A plain `Protocol`, not an ABC. `HunarClient` was written before this file and
satisfies it without inheriting anything, which is the point: neither
implementation knows the interface exists.
"""

from typing import Protocol, runtime_checkable

from app.config import settings
from app.integrations.hunar.types import (
    Agent,
    AgentCreate,
    BulkCallCreate,
    Call,
    CallCreate,
    Page,
    PhoneNumber,
)


@runtime_checkable
class VoiceProvider(Protocol):
    async def create_agent(self, payload: AgentCreate) -> Agent: ...

    async def get_agent(self, agent_id: str) -> Agent: ...

    async def create_call(self, payload: CallCreate) -> Call: ...

    async def create_bulk_calls(self, payload: BulkCallCreate) -> list[Call]: ...

    async def get_call(self, call_id: str) -> Call: ...

    async def list_calls(
        self,
        *,
        campaign_id: str | None = None,
        agent_id: list[str] | None = None,
        status: list[str] | None = None,
        page: int = 1,
        page_size: int | None = None,
    ) -> Page[Call]: ...

    async def list_numbers(
        self, *, page: int = 1, page_size: int | None = None
    ) -> Page[PhoneNumber]: ...


_provider: VoiceProvider | None = None


def get_voice_provider() -> VoiceProvider:
    """One instance for the process lifetime.

    Not a new object per call: `HunarClient` owns an httpx connection pool, and
    the simulator's entire state is in memory, so a fresh instance would lose
    every call it had ever placed.
    """
    global _provider
    if _provider is None:
        if settings.demo_mode:
            from app.integrations.hunar.simulator import HunarSimulator

            _provider = HunarSimulator()
        else:
            from app.integrations.hunar.client import HunarClient

            _provider = HunarClient(
                settings.hunar_api_key, base_url=settings.hunar_base_url
            )
    return _provider


def reset_voice_provider() -> None:
    """Test helper. The singleton would otherwise leak state between tests."""
    global _provider
    _provider = None
