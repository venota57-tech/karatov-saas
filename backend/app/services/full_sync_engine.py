from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, run_lightweight_migrations
from app.models import SyncCursor
from app.services.operations_sync_service import OperationsSyncService
from app.services.ozon_sync_service import _ozon_status, sync_ozon_block, get_ozon_status
from app.services.sync_service import get_sync_status, run_sync_wb_block_with_status

WB_BLOCKS = ["feedbacks_unanswered", "questions_unanswered", "feedbacks_answered", "questions_answered", "feedbacks_archive"]
OZON_BLOCKS = ["reviews_unanswered", "reviews_answered", "questions_unanswered", "questions_answered"]


def _now() -> datetime:
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
    row.updated_at = _now()
    if status in {"active", "finished"} and not error:
        row.last_success_at = _now()
    db.commit()


def _inject_ozon_cursor_from_db(db: Session, block: str) -> None:
    cursor_key = f"{block}:last_id"
    row = _cursor(db, "OZON", block)
    if row.cursor:
        _ozon_status.setdefault("cursors", {})[cursor_key] = row.cursor


def _persist_ozon_cursor_to_db(db: Session, block: str, result: dict[str, Any]) -> None:
    cursor = result.get("finish_last_id")
    diag = result.get("diagnostics") or {}
    done = bool(diag.get("end_reached")) or bool(result.get("done"))
    status = "finished" if done else "active"
    if cursor or done:
        _save_cursor(db, "OZON", block, cursor, status, {"result": result})


async def sync_ozon_block_until_done(db: Session, block: str, max_runs: int = 200) -> dict[str, Any]:
    _inject_ozon_cursor_from_db(db, block)

    total = {"platform": "OZON", "block": block, "runs": 0, "received": 0, "created": 0, "updated": 0, "results": [], "done": False}

    for _ in range(max_runs):
        res = await sync_ozon_block(db, block)
        total["runs"] += 1
        total["received"] += int(res.get("received", 0) or 0)
        total["created"] += int(res.get("created", 0) or 0)
        total["updated"] += int(res.get("updated", 0) or 0)
        total["results"].append(res)
        _persist_ozon_cursor_to_db(db, block, res)

        diag = res.get("diagnostics") or {}
        if diag.get("end_reached") or not res.get("received"):
            total["done"] = True
            break

        if block.startswith("questions_") and not (diag.get("last_id") or res.get("finish_last_id")):
            total["done"] = True
            total["note"] = "Question endpoint returned no cursor; imported one page to avoid repeating same slice forever."
            break

    return total


async def sync_ozon_full(db: Session, max_runs_per_block: int = 200) -> dict[str, Any]:
    if not settings.ozon_client_id or not settings.ozon_api_key:
        return {"ok": False, "platform": "OZON", "status": "not_connected", "error": "OZON_CLIENT_ID/OZON_API_KEY are not configured"}

    results = []
    for block in OZON_BLOCKS:
        try:
            results.append(await sync_ozon_block_until_done(db, block, max_runs=max_runs_per_block))
        except Exception as exc:
            _save_cursor(db, "OZON", block, _cursor(db, "OZON", block).cursor, "failed", error=str(exc))
            results.append({"platform": "OZON", "block": block, "status": "failed", "error": str(exc)})

    return {"ok": True, "platform": "OZON", "status": "done", "results": results, "live_status": get_ozon_status()}


async def sync_wb_full(db: Session, cycles: int = 25) -> dict[str, Any]:
    results = []
    for cycle in range(max(1, int(cycles))):
        for block in WB_BLOCKS:
            try:
                res = await run_sync_wb_block_with_status(block, db=db, source=f"full_sync_cycle_{cycle+1}")
                results.append(res)
            except Exception as exc:
                results.append({"platform": "WB", "block": block, "status": "failed", "cycle": cycle + 1, "error": str(exc)})
            await asyncio.sleep(max(0.5, float(settings.wb_request_pause_seconds or 1)))
    return {"ok": True, "platform": "WB", "status": "done", "cycles": cycles, "results": results, "live_status": get_sync_status()}


async def sync_operations_full(db: Session, platform: str = "ALL") -> dict[str, Any]:
    service = OperationsSyncService(db)
    result = await service.sync(platform=platform)

    supported = {"WB": {"return"}, "OZON": {"return", "act"}}
    requested = {"return", "act", "shortage", "surplus", "anonymization", "discrepancy", "defect"}
    blocks = result.setdefault("blocks_truth", [])

    for platform_name in ["WB", "OZON"]:
        if platform.upper() not in {"ALL", platform_name}:
            continue
        for kind in sorted(requested - supported.get(platform_name, set())):
            blocks.append({
                "platform": platform_name,
                "operation_type": kind,
                "status": "not_supported_yet",
                "message": "No stable implemented API adapter for this operation type yet; no fake rows created.",
            })

    return result


async def enrich_published_answers_full(db: Session, limit: int = 5000) -> dict[str, Any]:
    try:
        from app.services.answer_enrichment_service import enrich_all_published_answers
        return enrich_all_published_answers(db, limit=limit)
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": str(exc)}


async def full_sync_all(db: Session | None = None) -> dict[str, Any]:
    run_lightweight_migrations()
    own = db is None
    session = db or SessionLocal()
    try:
        wb = await sync_wb_full(session)
        ozon = await sync_ozon_full(session)
        operations = await sync_operations_full(session, platform="ALL")
        answers = await enrich_published_answers_full(session)
        return {
            "ok": True,
            "status": "done",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "wb": wb,
            "ozon": ozon,
            "operations": operations,
            "answers": answers,
        }
    finally:
        if own:
            session.close()
