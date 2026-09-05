import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import context
from app.db import db_url
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without this, autogenerate never notices a VARCHAR(200) becoming
        # VARCHAR(400), which is exactly the kind of change we will make.
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# Builds its own engine from the shared `db_url`, so the URL and the PgBouncer
# settings still have one definition, but the connections do not.
#
# It must NOT reuse the app's engine: migrations also run at startup (see
# main.py) inside a worker thread with its own event loop, and asyncpg
# connections are bound to the loop that created them. Sharing the pool across
# the two loops produces "attached to a different loop" errors that only appear
# in production.
async def run_migrations() -> None:
    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


# Offline (--sql) mode is deliberately not supported: we always migrate against
# a live database, and an unused code path is a code path that rots.
asyncio.run(run_migrations())
