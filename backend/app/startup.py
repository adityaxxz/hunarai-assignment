"""Schema migration at application startup.

Render's free tier has no pre-deploy hook — that is a paid feature — so
`alembic upgrade head` has to run somewhere. Running it here means a deploy can
never serve traffic against a schema older than the code.

The trade-offs, both real:

* **It races if more than one instance starts at once.** Alembic takes a lock, so
  the loser waits rather than corrupting anything, but on a larger deployment
  this belongs in a pre-deploy step. We run exactly one free instance, which is
  also why the start command pins a single worker.
* **It runs on every cold start**, which on the free tier is often. That is
  acceptable because `upgrade head` is a no-op when the revision already matches:
  Alembic reads `alembic_version`, finds nothing to apply, and returns. The cost
  is one extra round trip on a boot that already takes 30-60 seconds.

A failure here is deliberately fatal. An app that boots against a schema it does
not understand fails later, in scattered places, in ways nobody traces back to
the deploy.
"""

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

# backend/, which is where alembic.ini and the alembic/ package live.
BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _upgrade_to_head() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")


async def run_migrations() -> None:
    """Run Alembic off the event loop.

    Alembic's API is synchronous and our `env.py` calls `asyncio.run()`, which
    refuses to start inside a running loop. A worker thread has no loop of its
    own, so `asyncio.run` there is fine — and it is why `env.py` builds its own
    engine rather than borrowing the app's, whose connections belong to this loop.
    """
    logger.info("applying database migrations")
    await asyncio.to_thread(_upgrade_to_head)
    logger.info("database schema is up to date")
