from __future__ import annotations

import json
from typing import Any

from redis import Redis
from sqlalchemy import desc

from app.config import settings
from app.database import Base, SessionLocal, engine, run_lightweight_migrations
from app.models import SyncJob


QUEUE_NAME = "karatov:jobs"


def _redis() -> Redis:
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is not configured")
    return Redis.from_url(settings.redis_url, decode_responses=True)


def ensure_job_tables() -> None:
    try:
        run_lightweight_migrations()
    except Exception:
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=engine)


def enqueue_job(job_type: str, platform: str | None = None, block: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_job_tables()

    db = SessionLocal()
    try:
        row = SyncJob(job_type=job_type, platform=platform, block=block, status="queued", payload=payload or {})
        db.add(row)
        db.commit()
        db.refresh(row)

        message = {"sync_job_id": row.id, "job_type": job_type, "platform": platform, "block": block, "payload": payload or {}}
        _redis().lpush(QUEUE_NAME, json.dumps(message, ensure_ascii=False))

        return {"ok": True, "job": {"id": row.id, "job_type": row.job_type, "platform": row.platform, "block": row.block, "status": row.status}}
    finally:
        db.close()


def list_jobs(limit: int = 50) -> dict[str, Any]:
    try:
        ensure_job_tables()
        db = SessionLocal()
        try:
            rows = db.query(SyncJob).order_by(desc(SyncJob.created_at)).limit(min(max(int(limit or 50), 1), 200)).all()
            return {
                "ok": True,
                "items": [
                    {
                        "id": x.id,
                        "job_type": x.job_type,
                        "platform": x.platform,
                        "block": x.block,
                        "status": x.status,
                        "payload": x.payload,
                        "result": x.result,
                        "last_error": x.last_error,
                        "created_at": x.created_at.isoformat() if x.created_at else None,
                        "started_at": x.started_at.isoformat() if x.started_at else None,
                        "finished_at": x.finished_at.isoformat() if x.finished_at else None,
                        "updated_at": x.updated_at.isoformat() if x.updated_at else None,
                    }
                    for x in rows
                ],
            }
        finally:
            db.close()
    except Exception as exc:
        return {"ok": False, "items": [], "error": str(exc)}
