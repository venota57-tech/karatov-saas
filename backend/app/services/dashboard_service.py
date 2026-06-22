from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.dashboard_snapshot_service import get_dashboard_snapshot, refresh_dashboard_snapshots


def build_dashboard(db: Session, platform: str | None = "ALL") -> dict[str, Any]:
    return get_dashboard_snapshot(db, platform)


def build_dashboard_snapshot(db: Session, platform: str | None = "ALL") -> dict[str, Any]:
    return get_dashboard_snapshot(db, platform)


def refresh_dashboard(db: Session) -> dict[str, Any]:
    return refresh_dashboard_snapshots(db)
