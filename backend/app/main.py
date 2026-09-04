from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

app = FastAPI(title="Hunar FDE Assignment API")

# A single explicit origin, never "*": the browser sends the app's shared-secret
# header on every call, and a wildcard origin cannot carry credentials anyway.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Also the keep-warm target for the cron-job.org ping every 10 minutes: Render's
# free tier spins the service down after 15 minutes idle with a 30-60s cold start.
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
