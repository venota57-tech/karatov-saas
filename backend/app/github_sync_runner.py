from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine, run_lightweight_migrations
from app.models import SyncCursor, SyncJob
from app.services.marketplace_analytics_service import compute_sla
from app.services.operations_sync_service import OperationsSyncService
from app.services.ozon_sync_service import _ozon_status, sync_ozon_block
from app.services.sync_service import run_sync_wb_block_with_status


OZON_REVIEW_BLOCKS = ["reviews_unanswered", "reviews_answered"]
OZON_QUESTION_BLOCKS = ["questions_unanswered", "questions_answered"]
WB_BLOCKS = ["feedbacks_unanswered", "questions_unanswered", "feedbacks_answered", "questions_answered", "feedbacks_archive"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _job(db: Session, job_type: str, platform: str = "ALL", block: str | None = None, payload: dict[str, Any] | None = None) -> SyncJob:
    row = SyncJob(job_type=job_type, platform=platform, block=block, status="running", payload=payload or {}, started_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _finish(db: Session, job: SyncJob, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    job.status = status
    job.result = result or {}
    job.last_error = error
    job.finished_at = _now()
    job.updated_at = _now()
    db.commit()


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
    row.updated_at = _now()
    if status in {"active", "finished"} and not error:
        row.last_success_at = _now()
    db.commit()


def _ensure_schema(db: Session) -> None:
    run_lightweight_migrations()
    dialect = engine.dialect.name
    if dialect == "postgresql":
        db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS marketplace_status VARCHAR(128)"))
        db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS cx_workflow_status VARCHAR(64) DEFAULT 'new_to_review'"))
        db.execute(text("UPDATE marketplace_operations SET status='synced' WHERE status='new'"))
        db.execute(text("UPDATE marketplace_operations SET cx_workflow_status='new_to_review' WHERE cx_workflow_status IS NULL"))
        db.commit()
    else:
        cols = {c["name"] for c in inspect(engine).get_columns("marketplace_operations")}
        if "marketplace_status" not in cols:
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN marketplace_status VARCHAR(128)"))
        if "cx_workflow_status" not in cols:
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN cx_workflow_status VARCHAR(64) DEFAULT 'new_to_review'"))
        db.execute(text("UPDATE marketplace_operations SET status='synced' WHERE status='new'"))
        db.execute(text("UPDATE marketplace_operations SET cx_workflow_status='new_to_review' WHERE cx_workflow_status IS NULL"))
        db.commit()


def _restore_ozon_cursor(db: Session, block: str, mode: str) -> None:
    cursor_key = f"{block}:last_id"
    if mode == "latest":
        _ozon_status.setdefault("cursors", {}).pop(cursor_key, None)
        return
    row = _cursor(db, "OZON", f"{block}:backfill")
    if row.cursor:
        _ozon_status.setdefault("cursors", {})[cursor_key] = row.cursor


def _persist_ozon_cursor(db: Session, block: str, mode: str, result: dict[str, Any]) -> None:
    cursor_key = f"{block}:last_id"
    cursor = result.get("finish_last_id") or _ozon_status.setdefault("cursors", {}).get(cursor_key)
    diag = result.get("diagnostics") or {}
    done = bool(diag.get("end_reached")) or not result.get("received")
    target_block = f"{block}:{mode}"
    if mode == "backfill":
        target_block = f"{block}:backfill"
    _save_cursor(db, "OZON", target_block, cursor, "finished" if done else "active", {"result": result})


async def _ozon_one_page(db: Session, block: str, mode: str) -> dict[str, Any]:
    from app.config import settings

    _restore_ozon_cursor(db, block, mode)

    old_pages = getattr(settings, "ozon_sync_pages_per_block_run", 1)
    try:
        settings.ozon_sync_pages_per_block_run = 1
        result = await sync_ozon_block(db, block)
    finally:
        settings.ozon_sync_pages_per_block_run = old_pages

    _persist_ozon_cursor(db, block, mode, result)
    return result


async def run_ozon(db: Session, max_pages: int) -> dict[str, Any]:
    result: dict[str, Any] = {"platform": "OZON", "blocks": [], "received": 0, "created": 0, "updated": 0}

    # Latest pass catches new rows at the top.
    for block in OZON_REVIEW_BLOCKS:
        block_result = await _ozon_one_page(db, block, "latest")
        result["blocks"].append({"mode": "latest", **block_result})

    # Backfill pass continues beyond the historic 1000-row wall.
    for block in OZON_REVIEW_BLOCKS:
        seen = set()
        for _ in range(max_pages):
            block_result = await _ozon_one_page(db, block, "backfill")
            result["blocks"].append({"mode": "backfill", **block_result})
            result["received"] += int(block_result.get("received", 0) or 0)
            result["created"] += int(block_result.get("created", 0) or 0)
            result["updated"] += int(block_result.get("updated", 0) or 0)

            cursor = block_result.get("finish_last_id")
            diag = block_result.get("diagnostics") or {}
            if diag.get("end_reached") or not block_result.get("received"):
                break
            if cursor in seen:
                result.setdefault("warnings", []).append(f"{block}: cursor repeated; stopping to avoid loop")
                break
            if cursor:
                seen.add(cursor)

    # Questions currently use existing account methods. If Ozon returns no cursor,
    # we import the current available page and log the limitation instead of looping.
    for block in OZON_QUESTION_BLOCKS:
        try:
            block_result = await sync_ozon_block(db, block)
            block_result["mode"] = "latest"
            block_result["note"] = "Question endpoint in current adapter has no persisted cursor; latest page imported."
            result["blocks"].append(block_result)
            result["received"] += int(block_result.get("received", 0) or 0)
            result["created"] += int(block_result.get("created", 0) or 0)
            result["updated"] += int(block_result.get("updated", 0) or 0)
        except Exception as exc:
            result["blocks"].append({"platform": "OZON", "block": block, "status": "failed", "error": str(exc)})

    return result


async def run_wb(db: Session, cycles: int) -> dict[str, Any]:
    result = {"platform": "WB", "cycles": cycles, "blocks": []}
    try:
        from app.services import sync_service as wbsvc
    except Exception:
        wbsvc = None

    for cycle in range(max(1, cycles)):
        for block in WB_BLOCKS:
            try:
                if wbsvc and hasattr(wbsvc, "_block_state"):
                    row = _cursor(db, "WB", f"{block}:page")
                    if row.cursor and block in {"feedbacks_answered", "questions_answered", "feedbacks_archive"}:
                        wbsvc._block_state.setdefault(block, {})["next_page"] = int(row.cursor)
                block_result = await run_sync_wb_block_with_status(block, db=db, source=f"github_actions_cycle_{cycle+1}")
                if wbsvc and hasattr(wbsvc, "_block_state"):
                    next_page = wbsvc._block_state.setdefault(block, {}).get("next_page", 0)
                    _save_cursor(db, "WB", f"{block}:page", str(next_page or 0), "active", {"last_result": block_result})
                result["blocks"].append(block_result)
            except Exception as exc:
                result["blocks"].append({"platform": "WB", "block": block, "cycle": cycle + 1, "status": "failed", "error": str(exc)})
    return result


async def run_operations(db: Session) -> dict[str, Any]:
    res = await OperationsSyncService(db).sync(platform="ALL")
    _ensure_schema(db)
    return res


async def run_answers(db: Session) -> dict[str, Any]:
    try:
        from app.services.answer_enrichment_service import enrich_all_published_answers
        return enrich_all_published_answers(db, limit=5000)
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": str(exc)}


async def run_all(kind: str) -> dict[str, Any]:
    db = SessionLocal()
    root_job = _job(db, "github_sync_runner", platform="ALL", payload={"kind": kind})
    max_ozon_pages = int(os.getenv("GITHUB_SYNC_MAX_OZON_PAGES", "80"))
    max_wb_cycles = int(os.getenv("GITHUB_SYNC_MAX_WB_CYCLES", "8"))

    try:
        _ensure_schema(db)
        result: dict[str, Any] = {"ok": True, "kind": kind, "started_at": root_job.started_at.isoformat() if root_job.started_at else None}

        if kind in {"all", "ozon"}:
            result["ozon"] = await run_ozon(db, max_pages=max_ozon_pages)
        if kind in {"all", "wb"}:
            result["wb"] = await run_wb(db, cycles=max_wb_cycles)
        if kind in {"all", "operations"}:
            result["operations"] = await run_operations(db)
        if kind in {"all", "answers"}:
            result["answers"] = await run_answers(db)
        if kind in {"all", "analytics"}:
            result["sla"] = {"ALL": compute_sla(db, "ALL"), "WB": compute_sla(db, "WB"), "OZON": compute_sla(db, "OZON")}

        _finish(db, root_job, "success", result=result)
        return result
    except Exception as exc:
        _finish(db, root_job, "failed", result={"kind": kind}, error=str(exc))
        raise
    finally:
        db.close()


def main() -> None:
    kind = (os.getenv("GITHUB_SYNC_KIND") or "all").strip().lower()
    if kind not in {"all", "ozon", "wb", "operations", "answers", "analytics"}:
        raise SystemExit(f"Unsupported GITHUB_SYNC_KIND={kind}")
    result = asyncio.run(run_all(kind))
    print(result)


if __name__ == "__main__":
    main()
