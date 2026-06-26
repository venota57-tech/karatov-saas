from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from app.database import SessionLocal

router = APIRouter(prefix="/sync-control", tags=["sync-control"])

_STATE: dict[str, dict[str, Any]] = {
    "customer_ops": {"running": False, "last_error": None, "last_result": None},
    "operations": {"running": False, "last_error": None, "last_result": None},
}
_TASKS: dict[str, asyncio.Task] = {}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _run_customer_ops(platform: str, mode: str, run_id: str):
    from app.services.customer_ops_service import CustomerOpsService
    db = SessionLocal()
    try:
        result = await CustomerOpsService(db).sync(platform=platform, mode=mode)
        _STATE["customer_ops"].update({"running": False, "run_id": run_id, "finished_at": _iso(), "last_success_at": _iso(), "last_error": None, "last_result": result})
    except Exception as exc:
        _STATE["customer_ops"].update({"running": False, "run_id": run_id, "finished_at": _iso(), "last_error": str(exc), "trace": traceback.format_exc()[-2500:]})
    finally:
        db.close()


async def _run_operations(platform: str, mode: str, run_id: str):
    from app.services.operations_sync_service import OperationsSyncService
    db = SessionLocal()
    try:
        result = await OperationsSyncService(db).sync(platform=platform)
        _STATE["operations"].update({"running": False, "run_id": run_id, "finished_at": _iso(), "last_success_at": _iso(), "last_error": None, "last_result": result})
    except Exception as exc:
        _STATE["operations"].update({"running": False, "run_id": run_id, "finished_at": _iso(), "last_error": str(exc), "trace": traceback.format_exc()[-2500:]})
    finally:
        db.close()


@router.post("/start")
async def start(kind: str, platform: str = "ALL", mode: str = "full"):
    kind = (kind or "").strip().lower()
    platform = (platform or "ALL").upper()
    if kind not in {"customer_ops", "operations"}:
        return {"ok": False, "started": False, "error": "kind must be customer_ops or operations"}

    task = _TASKS.get(kind)
    if task is not None and not task.done():
        return {"ok": True, "started": False, "already_running": True, "status": _STATE[kind]}

    run_id = str(uuid4())
    _STATE[kind].update({"running": True, "run_id": run_id, "platform": platform, "mode": mode, "started_at": _iso(), "finished_at": None, "last_error": None, "last_result": None})
    _TASKS[kind] = asyncio.create_task(_run_customer_ops(platform, mode, run_id) if kind == "customer_ops" else _run_operations(platform, mode, run_id))
    return {"ok": True, "started": True, "already_running": False, "status": _STATE[kind]}


@router.get("/status")
def status(kind: str | None = None):
    if kind:
        return _STATE.get(kind.strip().lower(), {"error": "unknown kind"})
    return _STATE
