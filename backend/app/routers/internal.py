"""Endpoints the external cron calls. Not part of the product surface.

Render's free tier has no scheduler and no worker process, so both jobs are
driven from cron-job.org hitting these two paths.
"""

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.services.reconcile import (
    ReconcileReport,
    reconcile_active_calls,
    reconcile_incomplete_calls,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


def require_internal_token(authorization: str = Header(default="")) -> None:
    """Bearer token, compared in constant time.

    A missing token is 503 rather than 401, for the same reason the webhook
    receiver returns 503 with no signing key: an empty configured token would
    make `compare_digest("", "")` true and open the endpoint to anyone. That is
    our misconfiguration, not the caller's bad credential.
    """
    if not settings.internal_api_token:
        logger.warning("INTERNAL_API_TOKEN is not set, refusing internal request")
        raise HTTPException(status_code=503, detail="Internal endpoints are not configured")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        token, settings.internal_api_token
    ):
        raise HTTPException(status_code=401, detail="Invalid internal token")


@router.post("/reconcile", dependencies=[Depends(require_internal_token)])
async def reconcile(session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    """Both loops. Returns counts so the cron log is diagnostic.

    A bare 200 tells you the endpoint was reachable and nothing else; these
    numbers tell you whether anything is actually moving.
    """
    active = await reconcile_active_calls(session)
    incomplete = await reconcile_incomplete_calls(session)
    report: ReconcileReport = active.merge(incomplete)

    logger.info(
        "reconcile examined=%d updated=%d gave_up=%d errors=%d quota_exhausted=%s",
        report.examined, report.updated, report.gave_up, report.errors,
        report.quota_exhausted,
    )
    return {
        "examined": report.examined,
        "updated": report.updated,
        "gave_up": report.gave_up,
        "errors": report.errors,
        "quota_exhausted": report.quota_exhausted,
        "notes": report.notes,
    }


@router.get("/health-ping")
def health_ping() -> dict[str, str]:
    """Keep-alive target. No auth, no database, no work.

    Its only job is to stop Render's free tier spinning down after 15 minutes
    idle, which costs a 30-60 second cold start on the next real request. It
    deliberately does not touch the database: waking the web service is the whole
    point, and pinging Neon on a schedule would just keep that awake too.
    """
    return {"status": "awake"}
