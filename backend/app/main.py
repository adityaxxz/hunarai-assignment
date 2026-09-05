from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    calls,
    campaigns,
    candidates,
    internal,
    requisitions,
    sourcing,
    webhooks,
)
from app.startup import run_migrations

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await run_migrations()
    yield


app = FastAPI(title="Hunar FDE Assignment API", version=VERSION, lifespan=lifespan)

# A single explicit origin, never "*": the browser sends the app's shared-secret
# header on every call, and a wildcard origin cannot carry credentials anyway.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhooks.router)
app.include_router(internal.router)
app.include_router(requisitions.router)
app.include_router(candidates.router)
app.include_router(campaigns.router)
app.include_router(calls.router)
app.include_router(sourcing.router)


# Also the keep-warm target for the cron-job.org ping every 10 minutes: Render's
# free tier spins the service down after 15 minutes idle with a 30-60s cold start.
@app.get("/health")
def health() -> dict[str, str | bool]:
    # Both switches are reported by the backend rather than read from a second
    # NEXT_PUBLIC_ variable in the frontend, so the banner cannot disagree with
    # what is actually wired up. `people_search_provider` is the live provider's
    # own name, not the env var echoed back: it is the only way to confirm from
    # outside that a deployed link cannot spend People Data Labs credits, and a
    # setting nobody can verify is a setting nobody should trust.
    from app.integrations.people_search.provider import get_people_search_provider

    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "people_search_provider": get_people_search_provider().name,
        "version": VERSION,
    }
