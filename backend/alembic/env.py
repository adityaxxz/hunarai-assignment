import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection

from alembic import context
from app.db import engine
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


# Reuses the application engine rather than building one from alembic.ini, so
# the connection URL and the PgBouncer settings have exactly one definition.
async def run_migrations() -> None:
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


# Offline (--sql) mode is deliberately not supported: we always migrate against
# a live database, and an unused code path is a code path that rots.
asyncio.run(run_migrations())
