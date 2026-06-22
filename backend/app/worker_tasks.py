from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.database import SessionLocal, run_lightweight_migrations
from app.models import SyncJob
from app.services.dashboard_service import build_dashboard


def _job(db, sync_job_id: int) -> SyncJob | None:
    return db.query(SyncJob).filter(SyncJob.id == sync_job_id).first()


def _mark(db, sync_job_id: int, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    row = _job(db, sync_job_id)
    if not row:
        return
    row.status = status
    row.updated_at = datetime.utcnow()
    if status == "running":
        row.started_at = datetime.utcnow()
        row.last_error = None
    if status in {"success", "failed"}:
        row.finished_at = datetime.utcnow()
    if result is not None:
        row.result = result
    if error is not None:
        row.last_error = error
    db.commit()


def run_sync_job(sync_job_id: int, job_type: str, platform: str | None = None, block: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    run_lightweight_migrations()
    db = SessionLocal()
    try:
        _mark(db, sync_job_id, "running")
        if job_type == "dashboard_refresh":
            result = {"ALL": build_dashboard(platform="ALL"), "WB": build_dashboard(platform="WB"), "OZON": build_dashboard(platform="OZON"), "YM": build_dashboard(platform="YM")}
            _mark(db, sync_job_id, "success", result=result)
            return result

        if job_type == "marketplace_os_refresh":
            result = {"dashboard": {"ALL": build_dashboard(platform="ALL"), "WB": build_dashboard(platform="WB"), "OZON": build_dashboard(platform="OZON"), "YM": build_dashboard(platform="YM")}, "note": "Marketplace OS refresh reads server totals; heavy external sync remains explicit."}
            _mark(db, sync_job_id, "success", result=result)
            return result

        if job_type == "wb_answer_enrichment":
            from app.services.answer_enrichment_service import enrich_wb_published_answers
            result = enrich_wb_published_answers(db, limit=int((payload or {}).get("limit", 1000)))
            _mark(db, sync_job_id, "success" if result.get("ok") else "failed", result=result, error=result.get("error"))
            return result

        if job_type == "ozon_answer_enrichment":
            from app.services.answer_enrichment_service import enrich_ozon_published_answers
            result = enrich_ozon_published_answers(db, limit=int((payload or {}).get("limit", 1000)))
            _mark(db, sync_job_id, "success" if result.get("ok") else "failed", result=result, error=result.get("error"))
            return result

        if job_type == "answer_enrichment_all":
            from app.services.answer_enrichment_service import enrich_all_published_answers
            result = enrich_all_published_answers(db, limit=int((payload or {}).get("limit", 1000)))
            _mark(db, sync_job_id, "success", result=result)
            return result

        if job_type == "ozon_full_sync":
            from app.services.ozon_sync_service import sync_ozon_all
            result = asyncio.run(sync_ozon_all(db))
            _mark(db, sync_job_id, "success", result=result)
            return result
        if job_type == "ozon_block":
            from app.services.ozon_sync_service import sync_ozon_block
            if not block:
                raise RuntimeError("block is required for ozon_block")
            result = asyncio.run(sync_ozon_block(db, block))
            _mark(db, sync_job_id, "success", result=result)
            return result
        raise RuntimeError(f"Unsupported job_type: {job_type}")
    except Exception as exc:
        _mark(db, sync_job_id, "failed", error=str(exc))
        raise
    finally:
        db.close()
