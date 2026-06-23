from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal, run_lightweight_migrations
from app.models import SyncCursor, SyncJob
from app.services.operations_sync_service import OperationsSyncService
from app.services.ozon_sync_service import _ozon_status, get_ozon_status, sync_ozon_block
from app.services.sync_service import get_sync_status, run_sync_wb_block_with_status

BLOCK_SEQUENCE: list[dict[str, str]] = [
    {"kind": "ozon", "platform": "OZON", "block": "reviews_unanswered"},
    {"kind": "ozon", "platform": "OZON", "block": "reviews_answered"},
    {"kind": "ozon", "platform": "OZON", "block": "questions_unanswered"},
    {"kind": "ozon", "platform": "OZON", "block": "questions_answered"},
    {"kind": "answers_ozon", "platform": "OZON", "block": "published_answers"},
    {"kind": "wb", "platform": "WB", "block": "feedbacks_unanswered"},
    {"kind": "wb", "platform": "WB", "block": "questions_unanswered"},
    {"kind": "wb", "platform": "WB", "block": "feedbacks_answered"},
    {"kind": "wb", "platform": "WB", "block": "questions_answered"},
    {"kind": "wb", "platform": "WB", "block": "feedbacks_archive"},
    {"kind": "answers_wb", "platform": "WB", "block": "published_answers"},
    {"kind": "operations", "platform": "ALL", "block": "operations"},
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cursor(db: Session, platform: str, block: str) -> SyncCursor:
    row = db.query(SyncCursor).filter(SyncCursor.platform == platform, SyncCursor.block == block).first()
    if not row:
        row = SyncCursor(platform=platform, block=block, status="active", payload={})
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _save_cursor(db: Session, platform: str, block: str, cursor: str | None, status: str, payload: dict[str, Any] | None = None, error: str | None = None) -> None:
    row = _cursor(db, platform, block)
    row.cursor = cursor
    row.status = status
    row.payload = payload or row.payload or {}
    row.last_error = error
    row.updated_at = _utcnow()
    if status in {"active", "finished"} and not error:
        row.last_success_at = _utcnow()
    db.commit()


def _next_block(db: Session, forced: str | None = None) -> dict[str, str]:
    if forced:
        for item in BLOCK_SEQUENCE:
            if item["block"] == forced or f"{item['platform']}:{item['block']}" == forced:
                return item
        raise ValueError(f"Unknown forced cron block: {forced}")

    row = _cursor(db, "CRON", "round_robin")
    try:
        idx = int(row.cursor or "0")
    except Exception:
        idx = 0

    item = BLOCK_SEQUENCE[idx % len(BLOCK_SEQUENCE)]
    row.cursor = str((idx + 1) % len(BLOCK_SEQUENCE))
    row.updated_at = _utcnow()
    row.status = "active"
    row.payload = {"last_selected": item}
    db.commit()
    return item


def _recent_running_job(db: Session) -> SyncJob | None:
    threshold = _utcnow() - timedelta(minutes=15)
    return (
        db.query(SyncJob)
        .filter(SyncJob.job_type == "cron_tick")
        .filter(SyncJob.status == "running")
        .filter(SyncJob.started_at.isnot(None))
        .filter(SyncJob.started_at >= threshold)
        .order_by(SyncJob.started_at.desc())
        .first()
    )


def _start_job(db: Session, item: dict[str, str]) -> SyncJob:
    job = SyncJob(
        job_type="cron_tick",
        platform=item["platform"],
        block=item["block"],
        status="running",
        payload=item,
        started_at=_utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _finish_job(db: Session, job: SyncJob, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    job.status = status
    job.result = result or {}
    job.last_error = error
    job.finished_at = _utcnow()
    job.updated_at = _utcnow()
    db.commit()


def _restore_ozon_cursor(db: Session, block: str) -> None:
    row = _cursor(db, "OZON", block)
    cursor_key = f"{block}:last_id"
    if row.cursor:
        _ozon_status.setdefault("cursors", {})[cursor_key] = row.cursor


def _persist_ozon_cursor(db: Session, block: str, result: dict[str, Any]) -> None:
    cursor_key = f"{block}:last_id"
    cursor = result.get("finish_last_id") or _ozon_status.setdefault("cursors", {}).get(cursor_key)
    diag = result.get("diagnostics") or {}
    finished = bool(diag.get("end_reached")) or not result.get("received")
    status = "finished" if finished else "active"
    _save_cursor(db, "OZON", block, cursor, status, {"result": result})


async def _run_ozon(db: Session, block: str) -> dict[str, Any]:
    _restore_ozon_cursor(db, block)
    result = await sync_ozon_block(db, block)
    _persist_ozon_cursor(db, block, result)
    return result


async def _run_wb(db: Session, block: str) -> dict[str, Any]:
    return await run_sync_wb_block_with_status(block, db=db, source="cron_tick")


async def _run_operations(db: Session) -> dict[str, Any]:
    result = await OperationsSyncService(db).sync(platform="ALL")
    result.setdefault("business_note", "Unsupported operation types must appear in block diagnostics, not as fake zero data.")
    return result


async def _run_answers(db: Session, platform: str) -> dict[str, Any]:
    try:
        if platform == "OZON":
            from app.services.answer_enrichment_service import enrich_ozon_published_answers
            return enrich_ozon_published_answers(db, limit=500)
        from app.services.answer_enrichment_service import enrich_wb_published_answers
        return enrich_wb_published_answers(db, limit=500)
    except Exception as exc:
        return {"ok": False, "platform": platform, "status": "failed", "error": str(exc)}


async def run_cron_tick(db: Session | None = None, forced: str | None = None) -> dict[str, Any]:
    run_lightweight_migrations()
    own = db is None
    session = db or SessionLocal()
    try:
        running = _recent_running_job(session)
        if running:
            return {
                "ok": True,
                "skipped": True,
                "reason": "cron_tick_already_running",
                "running_job_id": running.id,
                "started_at": running.started_at.isoformat() if running.started_at else None,
            }

        item = _next_block(session, forced)
        job = _start_job(session, item)

        try:
            if item["kind"] == "ozon":
                result = await _run_ozon(session, item["block"])
            elif item["kind"] == "wb":
                result = await _run_wb(session, item["block"])
            elif item["kind"] == "operations":
                result = await _run_operations(session)
            elif item["kind"] == "answers_ozon":
                result = await _run_answers(session, "OZON")
            elif item["kind"] == "answers_wb":
                result = await _run_answers(session, "WB")
            else:
                raise ValueError(f"Unknown cron item kind: {item['kind']}")

            _finish_job(session, job, "success" if not result.get("error") else "failed", result=result, error=result.get("error"))
            return {"ok": True, "job_id": job.id, "selected": item, "result": result}
        except Exception as exc:
            error = str(exc)
            _save_cursor(session, item["platform"], item["block"], _cursor(session, item["platform"], item["block"]).cursor, "failed", error=error)
            _finish_job(session, job, "failed", result={"selected": item}, error=error)
            return {"ok": False, "job_id": job.id, "selected": item, "error": error}
    finally:
        if own:
            session.close()


def cron_status(db: Session) -> dict[str, Any]:
    cursors = db.query(SyncCursor).order_by(SyncCursor.platform, SyncCursor.block).all()
    jobs = (
        db.query(SyncJob)
        .filter(SyncJob.job_type == "cron_tick")
        .order_by(SyncJob.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "ok": True,
        "mode": "free_cron_pulse",
        "block_sequence": BLOCK_SEQUENCE,
        "cursors": [
            {
                "platform": c.platform,
                "block": c.block,
                "cursor": c.cursor,
                "status": c.status,
                "last_error": c.last_error,
                "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                "payload": c.payload,
            }
            for c in cursors
        ],
        "jobs": [
            {
                "id": j.id,
                "platform": j.platform,
                "block": j.block,
                "status": j.status,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "last_error": j.last_error,
                "result": j.result,
            }
            for j in jobs
        ],
        "live_sync_status": {"wb": get_sync_status(), "ozon": get_ozon_status()},
    }
