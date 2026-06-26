from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SyncCursor, SyncJob

router = APIRouter(tags=["sync-truth"])

WB_BLOCKS = ["feedbacks_unanswered", "questions_unanswered", "feedbacks_answered", "questions_answered", "feedbacks_archive"]
OZON_BLOCKS = ["reviews_unanswered", "reviews_answered", "questions_unanswered", "questions_answered", "published_answers"]


def _latest_job(db: Session, block: str | None = None):
    q = db.query(SyncJob).filter(SyncJob.job_type.in_(["github_sync_runner", "github_sync_block"]))
    if block:
        q = q.filter(SyncJob.block == block)
    return q.order_by(SyncJob.created_at.desc()).first()


def _cursor_payload(db: Session, platform: str, block: str) -> dict[str, Any]:
    variants = [block, f"{block}:page", f"{block}:latest", f"{block}:backfill"]
    rows = db.query(SyncCursor).filter(SyncCursor.platform == platform).filter(SyncCursor.block.in_(variants)).order_by(SyncCursor.updated_at.desc()).all()
    row = rows[0] if rows else None
    payload = row.payload if row and isinstance(row.payload, dict) else {}
    result = payload.get("result") or payload.get("last_result") or {}
    return {
        "status": row.status if row else "never_run",
        "last_finished_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "last_success_at": row.last_success_at.isoformat() if row and row.last_success_at else None,
        "last_error": row.last_error if row else None,
        "last_result": result or None,
    }


@router.get("/sync/status")
def wb_status(db: Session = Depends(get_db)):
    blocks_state = {block: _cursor_payload(db, "WB", block) for block in WB_BLOCKS}
    last = _latest_job(db, "wb_fast") or _latest_job(db, "wb_archive") or _latest_job(db, "wb_answered")
    return {
        "auto_sync_enabled": True,
        "runner_mode": "github_actions_split_cadence",
        "running": bool(last and last.status == "running"),
        "last_started_at": last.started_at.isoformat() if last and last.started_at else None,
        "last_finished_at": last.finished_at.isoformat() if last and last.finished_at else None,
        "last_success_at": last.finished_at.isoformat() if last and last.status in {"success", "partial"} and last.finished_at else None,
        "last_error": last.last_error if last else None,
        "last_result": last.result if last else None,
        "blocks_state": blocks_state,
        "enabled_blocks": WB_BLOCKS,
        "sweep_blocks": WB_BLOCKS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/sync/ozon/status")
def ozon_status(db: Session = Depends(get_db)):
    blocks = {block: _cursor_payload(db, "OZON", block) for block in OZON_BLOCKS}
    cursors = {c.block: {"cursor": c.cursor, "status": c.status, "last_error": c.last_error, "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None, "updated_at": c.updated_at.isoformat() if c.updated_at else None} for c in db.query(SyncCursor).filter(SyncCursor.platform == "OZON").all()}
    last = _latest_job(db, "ozon_latest") or _latest_job(db, "ozon_backfill")
    return {
        "enabled": True,
        "runner_mode": "github_actions_split_cadence",
        "last_started_at": last.started_at.isoformat() if last and last.started_at else None,
        "last_finished_at": last.finished_at.isoformat() if last and last.finished_at else None,
        "last_success_at": last.finished_at.isoformat() if last and last.status in {"success", "partial"} and last.finished_at else None,
        "last_error": last.last_error if last else None,
        "last_result": last.result if last else None,
        "blocks": blocks,
        "cursors": cursors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
