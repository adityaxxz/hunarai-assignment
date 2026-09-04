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
