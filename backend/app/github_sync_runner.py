from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, OperationalError, PendingRollbackError
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine, run_lightweight_migrations
from app.models import SyncCursor, SyncJob
from app.services.marketplace_analytics_service import compute_sla
from app.services.operations_sync_service import OperationsSyncService
from app.services.ozon_sync_service import _ozon_status, sync_ozon_block
from app.services.sync_service import run_sync_wb_block_with_status

OZON_REVIEW_BLOCKS = ["reviews_unanswered", "reviews_answered"]
OZON_QUESTION_BLOCKS = ["questions_unanswered", "questions_answered"]
WB_FAST_BLOCKS = ["feedbacks_unanswered", "questions_unanswered"]
WB_ANSWER_BLOCKS = ["feedbacks_answered", "questions_answered"]
WB_ARCHIVE_BLOCKS = ["feedbacks_archive"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_session() -> Session:
    return SessionLocal()


def _safe_close(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass
    try:
        db.close()
    except Exception:
        pass


def _is_disconnect(exc: BaseException) -> bool:
    value = str(exc).lower()
    return isinstance(exc, (OperationalError, DBAPIError, PendingRollbackError)) or any(
        token in value
        for token in [
            "ssl connection has been closed",
            "server closed the connection",
            "connection already closed",
            "pendingrollback",
            "terminating connection",
            "could not reconnect",
        ]
    )


def _dispose() -> None:
    try:
        engine.dispose()
    except Exception:
        pass


def _with_db_retry(label: str, fn: Callable[[Session], Any], attempts: int = 4) -> Any:
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        db = _new_session()
        try:
            result = fn(db)
            db.commit()
            db.close()
            return result
        except Exception as exc:
            last = exc
            _safe_close(db)
            if not _is_disconnect(exc) or attempt >= attempts:
                raise
            _dispose()
            print(f"[db-retry] {label}: {exc}; retry {attempt}/{attempts}", flush=True)
            time.sleep(min(2 * attempt, 8))
    raise last  # type: ignore[misc]


def _create_job(job_type: str, platform: str = "ALL", block: str | None = None, payload: dict[str, Any] | None = None) -> int:
    def work(db: Session) -> int:
        row = SyncJob(job_type=job_type, platform=platform, block=block, status="running", payload=payload or {}, started_at=_now())
        db.add(row)
        db.flush()
        return int(row.id)
    return _with_db_retry(f"create_job:{job_type}:{platform}:{block}", work)


def _finish_job(job_id: int, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    def work(db: Session) -> None:
        row = db.query(SyncJob).filter(SyncJob.id == job_id).first()
        if not row:
            return
        row.status = status
        row.result = result or {}
        row.last_error = error
        row.finished_at = _now()
        row.updated_at = _now()
    _with_db_retry(f"finish_job:{job_id}", work)


def _cursor_row(db: Session, platform: str, block: str) -> SyncCursor:
    row = db.query(SyncCursor).filter(SyncCursor.platform == platform, SyncCursor.block == block).first()
    if not row:
        row = SyncCursor(platform=platform, block=block, status="active", payload={})
        db.add(row)
        db.flush()
    return row


def _read_cursor(platform: str, block: str) -> str | None:
    def work(db: Session) -> str | None:
        return _cursor_row(db, platform, block).cursor
    return _with_db_retry(f"read_cursor:{platform}:{block}", work)


def _save_cursor(platform: str, block: str, cursor: str | None, status: str, payload: dict[str, Any] | None = None, error: str | None = None) -> None:
    def work(db: Session) -> None:
        row = _cursor_row(db, platform, block)
        row.cursor = cursor
        row.status = status
        row.payload = payload or row.payload or {}
        row.last_error = error
        row.updated_at = _now()
        if status in {"active", "finished"} and not error:
            row.last_success_at = _now()
    _with_db_retry(f"save_cursor:{platform}:{block}", work)


def _ensure_schema_once() -> None:
    def work(db: Session) -> None:
        run_lightweight_migrations()
        if engine.dialect.name == "postgresql":
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS marketplace_status VARCHAR(128)"))
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS cx_workflow_status VARCHAR(64) DEFAULT 'new_to_review'"))
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS document_number VARCHAR(128)"))
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS document_date TIMESTAMP"))
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS supply_id VARCHAR(128)"))
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS posting_number VARCHAR(128)"))
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS total_amount NUMERIC"))
        else:
            cols = {c["name"] for c in inspect(engine).get_columns("marketplace_operations")}
            if "marketplace_status" not in cols:
                db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN marketplace_status VARCHAR(128)"))
            if "cx_workflow_status" not in cols:
                db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN cx_workflow_status VARCHAR(64) DEFAULT 'new_to_review'"))
        db.execute(text("UPDATE marketplace_operations SET status='synced' WHERE status='new'"))
        db.execute(text("UPDATE marketplace_operations SET cx_workflow_status='new_to_review' WHERE cx_workflow_status IS NULL"))
    _with_db_retry("ensure_schema", work)


def _restore_ozon_cursor(block: str, mode: str) -> None:
    key = f"{block}:last_id"
    if mode == "latest":
        _ozon_status.setdefault("cursors", {}).pop(key, None)
        return
    cursor = _read_cursor("OZON", f"{block}:backfill")
    if cursor:
        _ozon_status.setdefault("cursors", {})[key] = cursor


def _persist_ozon_cursor(block: str, mode: str, result: dict[str, Any]) -> None:
    key = f"{block}:last_id"
    cursor = result.get("finish_last_id") or _ozon_status.setdefault("cursors", {}).get(key)
    diag = result.get("diagnostics") or {}
    done = bool(diag.get("end_reached")) or not result.get("received")
    target = f"{block}:latest" if mode == "latest" else f"{block}:backfill"
    _save_cursor("OZON", target, cursor, "finished" if done else "active", {"result": result})


async def _ozon_page(block: str, mode: str) -> dict[str, Any]:
    from app.config import settings
    _restore_ozon_cursor(block, mode)
    old_pages = getattr(settings, "ozon_sync_pages_per_block_run", 1)
    old_take = getattr(settings, "ozon_sync_take", 100)
    old_timeout = getattr(settings, "ozon_request_timeout_seconds", 30)

    async def run_once() -> dict[str, Any]:
        db = _new_session()
        try:
            settings.ozon_sync_pages_per_block_run = 1
            settings.ozon_sync_take = int(os.getenv("GITHUB_SYNC_OZON_TAKE", "25"))
            settings.ozon_request_timeout_seconds = min(float(old_timeout or 30), 18.0)
            result = await sync_ozon_block(db, block)
            db.commit()
            return result
        except Exception:
            _safe_close(db)
            raise
        finally:
            settings.ozon_sync_pages_per_block_run = old_pages
            settings.ozon_sync_take = old_take
            settings.ozon_request_timeout_seconds = old_timeout
            try:
                db.close()
            except Exception:
                pass

    last: BaseException | None = None
    for attempt in range(1, 4):
        try:
            result = await run_once()
            _persist_ozon_cursor(block, mode, result)
            return result
        except Exception as exc:
            last = exc
            if not _is_disconnect(exc) or attempt >= 3:
                _save_cursor("OZON", f"{block}:{mode}", _read_cursor("OZON", f"{block}:backfill"), "failed", error=str(exc))
                raise
            _dispose()
            print(f"[db-retry] Ozon {block}/{mode}: {exc}; retry {attempt}/3", flush=True)
            await asyncio.sleep(min(2 * attempt, 8))
    raise last  # type: ignore[misc]


async def run_ozon_latest() -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "platform": "OZON", "mode": "latest", "blocks": [], "received": 0, "created": 0, "updated": 0}
    for block in OZON_REVIEW_BLOCKS:
        try:
            res = await _ozon_page(block, "latest")
            result["blocks"].append({"stage": "latest", **res})
            result["received"] += int(res.get("received", 0) or 0)
            result["created"] += int(res.get("created", 0) or 0)
            result["updated"] += int(res.get("updated", 0) or 0)
        except Exception as exc:
            result["ok"] = False
            result["blocks"].append({"platform": "OZON", "block": block, "stage": "latest", "status": "failed", "error": str(exc)})
    for block in OZON_QUESTION_BLOCKS:
        db = _new_session()
        try:
            qres = await sync_ozon_block(db, block)
            db.commit()
            result["blocks"].append({"stage": "latest", **qres})
            result["received"] += int(qres.get("received", 0) or 0)
            result["created"] += int(qres.get("created", 0) or 0)
            result["updated"] += int(qres.get("updated", 0) or 0)
        except Exception as exc:
            _safe_close(db)
            result["ok"] = False
            result["blocks"].append({"platform": "OZON", "block": block, "status": "failed", "error": str(exc)})
        finally:
            try:
                db.close()
            except Exception:
                pass
    return result


async def run_ozon_backfill(max_pages: int) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "platform": "OZON", "mode": "backfill", "blocks": [], "received": 0, "created": 0, "updated": 0}
    for block in OZON_REVIEW_BLOCKS:
        seen: set[str | None] = set()
        for _ in range(max_pages):
            try:
                page = await _ozon_page(block, "backfill")
            except Exception as exc:
                result["ok"] = False
                result["blocks"].append({"platform": "OZON", "block": block, "stage": "backfill", "status": "failed", "error": str(exc)})
                break
            result["blocks"].append({"stage": "backfill", **page})
            result["received"] += int(page.get("received", 0) or 0)
            result["created"] += int(page.get("created", 0) or 0)
            result["updated"] += int(page.get("updated", 0) or 0)
            cursor = page.get("finish_last_id")
            diag = page.get("diagnostics") or {}
            if diag.get("end_reached") or not page.get("received"):
                break
            if cursor in seen:
                result.setdefault("warnings", []).append(f"{block}: cursor repeated; stopping to avoid loop")
                break
            if cursor:
                seen.add(cursor)
    return result


async def _wb_block(block: str, cycle: int) -> dict[str, Any]:
    try:
        from app.services import sync_service as wbsvc
    except Exception:
        wbsvc = None
    if wbsvc and hasattr(wbsvc, "_block_state"):
        saved = _read_cursor("WB", f"{block}:page")
        if saved and block in set(WB_ANSWER_BLOCKS + WB_ARCHIVE_BLOCKS):
            wbsvc._block_state.setdefault(block, {})["next_page"] = int(saved)
    db = _new_session()
    try:
        res = await run_sync_wb_block_with_status(block, db=db, source=f"github_actions_cycle_{cycle}")
        db.commit()
    except Exception:
        _safe_close(db)
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass
    if wbsvc and hasattr(wbsvc, "_block_state"):
        next_page = wbsvc._block_state.setdefault(block, {}).get("next_page", 0)
        cursor_block = f"{block}:page" if block in set(WB_ANSWER_BLOCKS + WB_ARCHIVE_BLOCKS) else block
        _save_cursor("WB", cursor_block, str(next_page or 0) if cursor_block.endswith(":page") else None, "active", {"last_result": res})
    return res


async def run_wb(blocks: list[str], cycles: int) -> dict[str, Any]:
    result = {"ok": True, "platform": "WB", "cycles": cycles, "blocks": []}
    for cycle in range(1, max(1, cycles) + 1):
        for block in blocks:
            try:
                result["blocks"].append(await _wb_block(block, cycle))
            except Exception as exc:
                result["ok"] = False
                result["blocks"].append({"platform": "WB", "block": block, "cycle": cycle, "status": "failed", "error": str(exc)})
    return result


async def run_operations() -> dict[str, Any]:
    db = _new_session()
    try:
        res = await OperationsSyncService(db).sync(platform="ALL")
        db.commit()
        return res
    except Exception:
        _safe_close(db)
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


async def run_answers() -> dict[str, Any]:
    db = _new_session()
    try:
        from app.services.answer_enrichment_service import enrich_all_published_answers
        res = enrich_all_published_answers(db, limit=5000)
        db.commit()
        return res
    except Exception as exc:
        _safe_close(db)
        return {"ok": False, "status": "failed", "error": str(exc)}
    finally:
        try:
            db.close()
        except Exception:
            pass


def run_sla() -> dict[str, Any]:
    db = _new_session()
    try:
        return {"ALL": compute_sla(db, "ALL"), "WB": compute_sla(db, "WB"), "OZON": compute_sla(db, "OZON")}
    finally:
        db.close()


async def run_stage(name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    job = _create_job("github_sync_block", platform="ALL", block=name, payload={"stage": name})
    try:
        res = await fn()
        _finish_job(job, "success" if res.get("ok", True) else "partial", result=res)
        return {"ok": bool(res.get("ok", True)), "result": res}
    except Exception as exc:
        err = str(exc)
        _finish_job(job, "failed", result={"stage": name}, error=err)
        return {"ok": False, "error": err}


async def run_all(kind: str) -> dict[str, Any]:
    root = _create_job("github_sync_runner", platform="ALL", payload={"kind": kind})
    max_ozon_pages = int(os.getenv("GITHUB_SYNC_MAX_OZON_PAGES", "40"))
    max_wb_cycles = int(os.getenv("GITHUB_SYNC_MAX_WB_CYCLES", "3"))
    _ensure_schema_once()
    result: dict[str, Any] = {"ok": True, "kind": kind, "started_at": _now().isoformat(), "stages": {}}

    if kind in {"all", "hot"}:
        result["stages"]["ozon_latest"] = await run_stage("ozon_latest", run_ozon_latest)
        result["stages"]["wb_fast"] = await run_stage("wb_fast", lambda: run_wb(WB_FAST_BLOCKS, cycles=max_wb_cycles))
    if kind in {"all", "answers"}:
        result["stages"]["answers"] = await run_stage("answers", run_answers)
        result["stages"]["wb_answered"] = await run_stage("wb_answered", lambda: run_wb(WB_ANSWER_BLOCKS, cycles=1))
    if kind in {"all", "backfill"}:
        result["stages"]["ozon_backfill"] = await run_stage("ozon_backfill", lambda: run_ozon_backfill(max_pages=max_ozon_pages))
        result["stages"]["wb_archive"] = await run_stage("wb_archive", lambda: run_wb(WB_ARCHIVE_BLOCKS + WB_ANSWER_BLOCKS, cycles=max_wb_cycles))
    if kind in {"all", "operations"}:
        result["stages"]["operations"] = await run_stage("operations", run_operations)
    if kind in {"all", "analytics", "answers"}:
        try:
            result["stages"]["analytics"] = {"ok": True, "result": run_sla()}
        except Exception as exc:
            result["stages"]["analytics"] = {"ok": False, "error": str(exc)}

    result["ok"] = any(v.get("ok") for v in result["stages"].values()) if result["stages"] else True
    result["status"] = "success" if all(v.get("ok") for v in result["stages"].values()) else "partial"
    _finish_job(root, result["status"], result=result, error=None if result["ok"] else "all stages failed")
    return result


def main() -> None:
    kind = (os.getenv("GITHUB_SYNC_KIND") or "all").strip().lower()
    allowed = {"all", "hot", "answers", "backfill", "operations", "analytics"}
    if kind not in allowed:
        raise SystemExit(f"Unsupported GITHUB_SYNC_KIND={kind}; allowed={sorted(allowed)}")
    print(asyncio.run(run_all(kind)), flush=True)


if __name__ == "__main__":
    main()
