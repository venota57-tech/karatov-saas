from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db, run_lightweight_migrations
from ..services.dashboard_snapshot_service import get_dashboard_snapshot, refresh_dashboard_snapshots_once

try:
    from ..services.sync_service import get_sync_status
except Exception:
    get_sync_status = None

try:
    from ..services.ozon_sync_service import get_ozon_status
except Exception:
    get_ozon_status = None


router = APIRouter(prefix="/system", tags=["system"])


@router.get("/migrate")
def migrate():
    return run_lightweight_migrations()


@router.post("/migrate")
def migrate_post():
    return run_lightweight_migrations()


@router.get("/diagnostics")
def diagnostics():
    return {
        "status": "ok",
        "source": "lightweight_diagnostics",
        "keys": {
            "openai_api_key": bool(settings.openai_api_key),
            "wb_api_key": bool(settings.wb_api_token),
            "wb_api_token": bool(settings.wb_api_token),
            "ozon_client_id": bool(settings.ozon_client_id),
            "ozon_api_key": bool(settings.ozon_api_key),
        },
        "sync": {
            "wb": get_sync_status() if get_sync_status else None,
            "ozon": get_ozon_status() if get_ozon_status else None,
            "ym": {"status": "not_connected"},
        },
        "note": "Diagnostics endpoint is intentionally lightweight and does not run migrations, DB aggregates or sync jobs.",
    }


@router.get("/dashboard")
def system_dashboard_endpoint(platform: str = "ALL", db: Session = Depends(get_db)):
    return get_dashboard_snapshot(db, platform)


@router.post("/dashboard/refresh")
def system_dashboard_refresh(background_tasks: BackgroundTasks):
    background_tasks.add_task(refresh_dashboard_snapshots_once)
    return {"ok": True, "status": "scheduled", "message": "Dashboard snapshot refresh scheduled in background."}
