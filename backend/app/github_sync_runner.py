from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, OperationalError
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


def _new_session() -> Session:
    return SessionLocal()


def _rollback_close(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass
    try:
        db.close()
    except Exception:
        pass


def _is_db_disconnect(exc: BaseException) -> bool:
    text_value = str(exc).lower()
    return isinstance(exc, (OperationalError, DBAPIError)) or any(
        token in text_value
        for token in [
            "ssl connection has been closed",
            "server closed the connection",
            "connection already closed",
            "could not reconnect",
            "pendingrollback",
            "terminating connection",
        ]
    )


def _with_db_retry(fn: Callable[[Session], Any], label: str, attempts: int = 3) -> Any:
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
            _rollback_close(db)
            if not _is_db_disconnect(exc) or attempt >= attempts:
                raise
            try:
                engine.dispose()
            except Exception:
                pass
            print(f"[db-retry] {label}: {exc}; retry {attempt}/{attempts}", flush=True)
            time.sleep(min(2 * attempt, 6))
    raise last  # type: ignore[misc]


def _job(job_type: str, platform: str = "ALL", block: str | None = None, payload: dict[str, Any] | None = None) -> int:
    def work(db: Session) -> int:
        row = SyncJob(job_type=job_type, platform=platform, block=block, status="running", payload=payload or {}, started_at=_now())
        db.add(row)
        db.flush()
        return int(row.id)
    return _with_db_retry(work, f"create_job:{job_type}")


def _finish(job_id: int, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
    def work(db: Session) -> None:
        row = db.query(SyncJob).filter(SyncJob.id == job_id).first()
        if not row:
            return
        row.status = status
        row.result = result or {}
        row.last_error = error
        row.finished_at = _now()
        row.updated_at = _now()
    _with_db_retry(work, f"finish_job:{job_id}")


def _cursor(db: Session, platform: str, block: str) -> SyncCursor:
    row = db.query(SyncCursor).filter(SyncCursor.platform == platform, SyncCursor.block == block).first()
    if not row:
        row = SyncCursor(platform=platform, block=block, status="active", payload={})
        db.add(row)
        db.flush()
    return row


def _read_cursor(platform: str, block: str) -> str | None:
    def work(db: Session) -> str | None:
        row = _cursor(db, platform, block)
        return row.cursor
    return _with_db_retry(work, f"read_cursor:{platform}:{block}")


def _save_cursor(platform: str, block: str, cursor: str | None, status: str, payload: dict[str, Any] | None = None, error: str | None = None) -> None:
    def work(db: Session) -> None:
        row = _cursor(db, platform, block)
        row.cursor = cursor
        row.status = status
        row.payload = payload or row.payload or {}
        row.last_error = error
        row.updated_at = _now()
        if status in {"active", "finished"} and not error:
            row.last_success_at = _now()
    _with_db_retry(work, f"save_cursor:{platform}:{block}")


def _ensure_schema_once() -> None:
    def work(db: Session) -> None:
        run_lightweight_migrations()
        dialect = engine.dialect.name
        if dialect == "postgresql":
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS marketplace_status VARCHAR(128)"))
            db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN IF NOT EXISTS cx_workflow_status VARCHAR(64) DEFAULT 'new_to_review'"))
            db.execute(text("UPDATE marketplace_operations SET status='synced' WHERE status='new'"))
            db.execute(text("UPDATE marketplace_operations SET cx_workflow_status='new_to_review' WHERE cx_workflow_status IS NULL"))
        else:
            cols = {c["name"] for c in inspect(engine).get_columns("marketplace_operations")}
            if "marketplace_status" not in cols:
                db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN marketplace_status VARCHAR(128)"))
            if "cx_workflow_status" not in cols:
                db.execute(text("ALTER TABLE marketplace_operations ADD COLUMN cx_workflow_status VARCHAR(64) DEFAULT 'new_to_review'"))
            db.execute(text("UPDATE marketplace_operations SET status='synced' WHERE status='new'"))
            db.execute(text("UPDATE marketplace_operations SET cx_workflow_status='new_to_review' WHERE cx_workflow_status IS NULL"))
    _with_db_retry(work, "ensure_schema")


def _restore_ozon_cursor(block: str, mode: str) -> None:
    cursor_key = f"{block}:last_id"
    if mode == "latest":
        _ozon_status.setdefault("cursors", {}).pop(cursor_key, None)
        return
    cursor = _read_cursor("OZON", f"{block}:backfill")
    if cursor:
        _ozon_status.setdefault("cursors", {})[cursor_key] = cursor


def _persist_ozon_cursor(block: str, mode: str, result: dict[str, Any]) -> None:
    cursor_key = f"{block}:last_id"
    cursor = result.get("finish_last_id") or _ozon_status.setdefault("cursors", {}).get(cursor_key)
    diag = result.get("diagnostics") or {}
    done = bool(diag.get("end_reached")) or not result.get("received")
    target_block = f"{block}:latest" if mode == "latest" else f"{block}:backfill"
    _save_cursor("OZON", target_block, cursor, "finished" if done else "active", {"result": result})


async def _ozon_one_page(block: str, mode: str) -> dict[str, Any]:
    from app.config import settings

    _restore_ozon_cursor(block, mode)
    old_pages = getattr(settings, "ozon_sync_pages_per_block_run", 1)
    old_timeout = getattr(settings, "ozon_request_timeout_seconds", 30)

    async def once() -> dict[str, Any]:
        db = _new_session()
        try:
            settings.ozon_sync_pages_per_block_run = 1
            settings.ozon_request_timeout_seconds = min(float(old_timeout or 30), 18.0)
            result = await sync_ozon_block(db, block)
            db.commit()
            return result
        except Exception:
            _rollback_close(db)
            raise
        finally:
            settings.ozon_sync_pages_per_block_run = old_pages
            settings.ozon_request_timeout_seconds = old_timeout
            try:
                db.close()
            except Exception:
                pass

    last: BaseException | None = None
    for attempt in range(1, 4):
        try:
            result = await once()
            _persist_ozon_cursor(block, mode, result)
            return result
        except Exception as exc:
            last = exc
            if not _is_db_disconnect(exc) or attempt >= 3:
                _save_cursor("OZON", f"{block}:{mode}", _read_cursor("OZON", f"{block}:backfill"), "failed", error=str(exc))
                raise
            try:
                engine.dispose()
            except Exception:
                pass
            print(f"[db-retry] ozon {block}/{mode}: {exc}; retry {attempt}/3", flush=True)
            await asyncio.sleep(min(2 * attempt, 6))
    raise last  # type: ignore[misc]


async def run_ozon(max_pages: int) -> dict[str, Any]:
    result: dict[str, Any] = {"platform": "OZON", "blocks": [], "received": 0, "created": 0, "updated": 0}

    for block in OZON_REVIEW_BLOCKS:
        block_result = await _ozon_one_page(block, "latest")
        result["blocks"].append({"mode": "latest", **block_result})

    for block in OZON_REVIEW_BLOCKS:
        seen: set[str | None] = set()
        for _ in range(max_pages):
            block_result = await _ozon_one_page(block, "backfill")
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

    for block in OZON_QUESTION_BLOCKS:
        try:
            db = _new_session()
            try:
                block_result = await sync_ozon_block(db, block)
                db.commit()
            except Exception:
                _rollback_close(db)
                raise
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            block_result["mode"] = "latest"
            block_result["note"] = "Question endpoint in current adapter has no persisted cursor; latest page imported."
            result["blocks"].append(block_result)
            result["received"] += int(block_result.get("received", 0) or 0)
            result["created"] += int(block_result.get("created", 0) or 0)
            result["updated"] += int(block_result.get("updated", 0) or 0)
        except Exception as exc:
            result["blocks"].append({"platform": "OZON", "block": block, "status": "failed", "error": str(exc)})

    return result


async def _wb_one_block(block: str, cycle: int) -> dict[str, Any]:
    try:
        from app.services import sync_service as wbsvc
    except Exception:
        wbsvc = None

    if wbsvc and hasattr(wbsvc, "_block_state"):
        page = _read_cursor("WB", f"{block}:page")
        if page and block in {"feedbacks_answered", "questions_answered", "feedbacks_archive"}:
            wbsvc._block_state.setdefault(block, {})["next_page"] = int(page)

    db = _new_session()
    try:
        block_result = await run_sync_wb_block_with_status(block, db=db, source=f"github_actions_cycle_{cycle}")
        db.commit()
    except Exception:
        _rollback_close(db)
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass

    if wbsvc and hasattr(wbsvc, "_block_state"):
        next_page = wbsvc._block_state.setdefault(block, {}).get("next_page", 0)
        _save_cursor("WB", f"{block}:page", str(next_page or 0), "active", {"last_result": block_result})
    return block_result


async def run_wb(cycles: int) -> dict[str, Any]:
    result = {"platform": "WB", "cycles": cycles, "blocks": []}
    for cycle in range(1, max(1, cycles) + 1):
        for block in WB_BLOCKS:
            try:
                result["blocks"].append(await _wb_one_block(block, cycle))
            except Exception as exc:
                result["blocks"].append({"platform": "WB", "block": block, "cycle": cycle, "status": "failed", "error": str(exc)})
    return result


async def run_operations() -> dict[str, Any]:
    db = _new_session()
    try:
        res = await OperationsSyncService(db).sync(platform="ALL")
        db.commit()
        return res
    except Exception:
        _rollback_close(db)
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
        result = enrich_all_published_answers(db, limit=5000)
        db.commit()
        return result
    except Exception as exc:
        _rollback_close(db)
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


async def run_all(kind: str) -> dict[str, Any]:
    root_job_id = _job("github_sync_runner", platform="ALL", payload={"kind": kind})
    max_ozon_pages = int(os.getenv("GITHUB_SYNC_MAX_OZON_PAGES", "40"))
    max_wb_cycles = int(os.getenv("GITHUB_SYNC_MAX_WB_CYCLES", "6"))

    try:
        _ensure_schema_once()
        result: dict[str, Any] = {"ok": True, "kind": kind, "started_at": _now().isoformat()}

        if kind in {"all", "ozon"}:
            result["ozon"] = await run_ozon(max_pages=max_ozon_pages)
        if kind in {"all", "wb"}:
            result["wb"] = await run_wb(cycles=max_wb_cycles)
        if kind in {"all", "operations"}:
            result["operations"] = await run_operations()
        if kind in {"all", "answers"}:
            result["answers"] = await run_answers()
        if kind in {"all", "analytics"}:
            result["sla"] = run_sla()

        _finish(root_job_id, "success", result=result)
        return result
    except Exception as exc:
        try:
            _finish(root_job_id, "failed", result={"kind": kind}, error=str(exc))
        except Exception as finish_exc:
            print(f"[runner] failed to persist failure status: {finish_exc}", flush=True)
        raise


def main() -> None:
    kind = (os.getenv("GITHUB_SYNC_KIND") or "all").strip().lower()
    if kind not in {"all", "ozon", "wb", "operations", "answers", "analytics"}:
        raise SystemExit(f"Unsupported GITHUB_SYNC_KIND={kind}")
    result = asyncio.run(run_all(kind))
    print(result)


if __name__ == "__main__":
    main()
