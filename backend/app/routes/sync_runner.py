from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.marketplace_analytics_service import compute_sla, runner_status

router = APIRouter(prefix="/sync-runner", tags=["sync-runner"])


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return runner_status(db)


@router.get("/sla")
def sla(platform: str = "ALL", db: Session = Depends(get_db)):
    return {"ok": True, "sla": compute_sla(db, platform)}
