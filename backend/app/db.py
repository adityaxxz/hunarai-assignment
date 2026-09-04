from collections.abc import AsyncIterator

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Neon's pooled endpoint is PgBouncer in transaction mode, which cannot keep
# asyncpg's server-side prepared statements alive between checkouts; leaving the
# cache on produces "prepared statement already exists" under concurrency. It is
# a dialect option, so it has to ride on the URL rather than a create_engine kwarg.
# Rejected the direct (unpooled) endpoint instead: Render restarts would churn
# through Neon's connection limit.
db_url = make_url(settings.database_url).update_query_dict(
    {"prepared_statement_cache_size": "0"}
)

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
