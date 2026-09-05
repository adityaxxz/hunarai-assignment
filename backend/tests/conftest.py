"""Test wiring.

The suite runs against in-memory SQLite so `pytest` needs no database server.
Production is Postgres; the JSON columns in models.py carry a
`with_variant(JSONB, "postgresql")` so the migration still emits JSONB and only
the tests see plain JSON.
"""

import os

# Set before app.config is imported: settings requires DATABASE_URL, and this
# guarantees the tests never construct an engine pointed at a real database even
# if a developer's .env is present.
os.environ["DATABASE_URL"] = "postgresql+asyncpg://unused:unused@localhost/unused"
# demo_mode off so the suite exercises the live verification path, which is the
# one with a security consequence. Demo mode signs with its own generated key.
os.environ["DEMO_MODE"] = "false"
os.environ["HUNAR_API_KEY"] = "test-webhook-signing-key"
# Pinned for the same reason DATABASE_URL is. A developer who switches their own
# .env to the live provider must not thereby make the test suite spend People
# Data Labs credits — the free tier is 100 records a month, and one full run of
# these tests would have taken a chunk of it. Caught by the socket guard below
# the first time it happened, which is what that guard is for. Tests that need
# the PDL client build it directly with an injected httpx transport.
os.environ["PEOPLE_SEARCH_PROVIDER"] = "fixture"
os.environ["PDL_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""

import socket  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.routers.webhooks import reset_rate_limit  # noqa: E402

SIGNING_KEY = os.environ["HUNAR_API_KEY"]


@pytest.fixture
async def session_factory():
    # StaticPool keeps every checkout on the same connection, without which each
    # ":memory:" connection would get its own empty database.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# Anything else is the internet.
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "", None}


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch):
    """Fail any test that opens a connection off this machine, naming the host.

    Permanent rather than an ad-hoc check, so a future task that introduces an
    outbound call fails on the spot instead of quietly reaching the internet —
    which is how the live Hunar calls in task 8 went unnoticed until a key
    happened to 401.

    Both hooks matter. `connect` catches the socket path, and `getaddrinfo`
    catches anything that resolves a hostname first, including asyncio transports
    on Windows that do not go through `socket.connect` at all.
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise AssertionError(
                f"This test opened a network connection to {host!r}. "
                "Tests must not reach outside this machine; use the simulator or "
                "an injected httpx transport."
            )
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in _LOOPBACK:
            raise AssertionError(
                f"This test resolved the hostname {host!r}. "
                "Tests must not reach outside this machine; use the simulator or "
                "an injected httpx transport."
            )
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def _never_call_hunar_for_real(monkeypatch):
    """Pin the VoiceProvider singleton to a simulator for every test.

    DEMO_MODE is false in this suite so the webhook tests exercise the live
    signing path, but that same flag drives `get_voice_provider()` — which means
    any router calling it would build a real HunarClient and put actual requests
    on the wire against api.voice.hunar.ai. Caught exactly that way: a test
    failed with "[401] Invalid API key", which is a response from Hunar.
    """
    import app.integrations.hunar.provider as provider_module
    from app.integrations.hunar.simulator import HunarSimulator

    async def no_deliver(event_type: str, body: dict) -> None:
        """Deliveries are exercised deliberately in test_simulator, not as a side
        effect of every other test."""

    monkeypatch.setattr(
        provider_module,
        "_provider",
        HunarSimulator(time_scale=0.01, deliver=no_deliver),
    )


@pytest.fixture(autouse=True)
async def _background_tasks_use_test_db(session_factory, monkeypatch):
    """`process_call_event` runs as a background task after the response, so it
    opens its own session rather than borrowing the request's. Without this it
    would open one against the real engine."""
    import app.services.call_state as call_state

    monkeypatch.setattr(call_state, "SessionLocal", session_factory)


@pytest.fixture
async def client(session_factory):
    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    reset_rate_limit()  # module-level window would otherwise leak between tests
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
async def session(session_factory) -> AsyncSession:
    async with session_factory() as s:
        yield s
