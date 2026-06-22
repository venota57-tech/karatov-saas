from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.queue_service import enqueue_job, list_jobs


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/status")
def jobs_status(limit: int = 50):
    return list_jobs(limit=limit)


@router.post("/enqueue")
def jobs_enqueue(job_type: str, platform: str | None = None, block: str | None = None):
    allowed = {"dashboard_refresh", "ozon_full_sync", "ozon_block"}
    if job_type not in allowed:
        raise HTTPException(400, f"Unsupported job_type: {job_type}")
    if job_type == "ozon_block" and not block:
        raise HTTPException(400, "block is required for ozon_block")
    return enqueue_job(job_type=job_type, platform=platform, block=block, payload={})
