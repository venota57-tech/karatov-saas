from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services.cron_pulse_service import cron_status, run_cron_tick

router = APIRouter(prefix="/cron", tags=["cron"])


def _check_token(token: str | None) -> None:
    if not settings.cron_secret:
        raise HTTPException(
            status_code=503,
            detail="CRON_SECRET is not configured. Add it to Render env and call /cron/tick?token=...",
        )
    if token != settings.cron_secret:
        raise HTTPException(status_code=403, detail="Invalid cron token")


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return cron_status(db)


@router.post("/tick")
async def tick(token: str | None = None, block: str | None = None, db: Session = Depends(get_db)):
    _check_token(token)
    return await run_cron_tick(db, forced=block)


@router.get("/tick")
async def tick_get(token: str | None = None, block: str | None = None, db: Session = Depends(get_db)):
    _check_token(token)
    return await run_cron_tick(db, forced=block)


@router.get("/wake")
def wake():
    return {"ok": True, "status": "awake", "mode": "free_cron_pulse"}
