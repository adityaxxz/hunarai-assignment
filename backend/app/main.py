from fastapi import FastAPI

app = FastAPI(title="Hunar FDE Assignment API")


# Also the keep-warm target for the cron-job.org ping every 10 minutes: Render's
# free tier spins the service down after 15 minutes idle with a 30-60s cold start.
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
