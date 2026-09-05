from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import internal, webhooks
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


# Also the keep-warm target for the cron-job.org ping every 10 minutes: Render's
# free tier spins the service down after 15 minutes idle with a 30-60s cold start.
@app.get("/health")
def health() -> dict[str, str | bool]:
    # demo_mode is reported by the backend rather than read from a second
    # NEXT_PUBLIC_ variable in the frontend, so the banner cannot disagree with
    # which voice provider is actually wired up.
    return {"status": "ok", "demo_mode": settings.demo_mode, "version": VERSION}
