from collections.abc import AsyncIterator

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

def _async_url(raw: str) -> URL:
    """Normalise whatever connection string the provider handed us.

    Neon, Render and Heroku all print `postgresql://` (and sometimes the legacy
    `postgres://`). SQLAlchemy resolves both to the *synchronous* psycopg2
    driver, which we do not install, so the app dies at import with a
    ModuleNotFoundError that says nothing about the real cause. Forcing the
    async driver here means the string can be pasted from the provider's
    dashboard unedited, which is what everyone will actually do.
    """
    url = make_url(raw)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+asyncpg")

    # asyncpg rejects libpq's parameter spellings, which Neon includes by
    # default. Translate rather than hardcode: `sslmode` becomes asyncpg's
    # `ssl`, so a provider URL that asks for TLS still gets it and a local
    # Postgres with no TLS is not forced into a connection it cannot make.
    sslmode = url.query.get("sslmode")
    query = {
        k: v for k, v in url.query.items() if k not in ("sslmode", "channel_binding")
    }
    if sslmode and "asyncpg" in url.drivername:
        query["ssl"] = sslmode
    # Neon's pooled endpoint is PgBouncer in transaction mode, which cannot keep
    # asyncpg's server-side prepared statements alive between checkouts; leaving
    # the cache on produces "prepared statement already exists" under
    # concurrency. Rejected the direct (unpooled) endpoint instead: Render
    # restarts would churn through Neon's connection limit.
    query["prepared_statement_cache_size"] = "0"
    return url.set(query=query)


db_url = _async_url(settings.database_url)

engine = create_async_engine(
    db_url,
    # Neon scales the compute to zero after 5 minutes idle. Without pre-ping the
    # first request after a resume gets a dead pooled connection and 500s; with
    # it, SQLAlchemy discards that connection and reopens, costing ~1s once.
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=0,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
