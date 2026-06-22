from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.queue_service import enqueue_job
from app.services.full_sync_engine import sync_operations_full

router = APIRouter(prefix="/full-sync", tags=["full-sync"])


@router.post("/enqueue")
def enqueue_full_sync(kind: str = "all"):
    allowed = {
        "all": "full_sync_all",
        "wb": "full_sync_wb",
        "ozon": "full_sync_ozon",
        "operations": "full_sync_operations",
        "answers": "answer_enrichment_all",
    }
    if kind not in allowed:
        return {"ok": False, "error": f"Unsupported kind: {kind}", "allowed": list(allowed)}
    return enqueue_job(job_type=allowed[kind], platform="ALL", payload={"kind": kind})


@router.post("/operations/run")
async def run_operations_sync(platform: str = "ALL", db: Session = Depends(get_db)):
    return await sync_operations_full(db, platform=platform)


@router.get("/plan")
def full_sync_plan():
    return {
        "ok": True,
        "architecture": "web enqueues; worker executes",
        "jobs": ["full_sync_all", "full_sync_wb", "full_sync_ozon", "full_sync_operations", "answer_enrichment_all"],
        "guarantees": [
            "No web startup marketplace loops",
            "Ozon review cursors persisted in DB SyncCursor",
            "WB backfill runs by worker cycles with per-block cooldown",
            "Published answers are enriched when marketplace APIs return them",
            "Unsupported operation blocks are reported as not_supported_yet instead of fake zero rows",
        ],
    }
