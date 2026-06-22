from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DashboardSnapshot, Review, Question


PLATFORMS = ["ALL", "WB", "OZON", "YM"]
REFRESH_INTERVAL_SECONDS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_platform(value: str | None) -> str:
    value = (value or "ALL").strip().upper()
    if value in {"WILDBERRIES", "WILDBERRY", "ВБ"}:
        return "WB"
    if value in {"OZON.RU", "ОЗОН"}:
        return "OZON"
    if value in {"YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС"}:
        return "YM"
    if value in {"ALL", "WB", "OZON", "YM"}:
        return value
    return value


def _aliases(platform: str) -> list[str]:
    platform = _normalize_platform(platform)
    if platform == "ALL":
        return []
    if platform == "WB":
        return ["WB", "WILDBERRIES", "WILDBERRY", "ВБ", "wb", "wildberries"]
    if platform == "OZON":
        return ["OZON", "OZON.RU", "ОЗОН", "ozon"]
    if platform == "YM":
        return ["YM", "YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС", "ym", "yandex"]
    return [platform]


def _apply_platform(q, model: Any, platform: str):
    aliases = _aliases(platform)
    if not aliases:
        return q
    return q.filter(func.upper(model.platform).in_([x.upper() for x in aliases]))


def _count(db: Session, model: Any, platform: str, *filters: Any) -> int:
    q = db.query(func.count(model.id))
    q = _apply_platform(q, model, platform)
    for item in filters:
        q = q.filter(item)
    return int(q.scalar() or 0)


def _avg_rating(db: Session, platform: str) -> float | None:
    q = db.query(func.avg(Review.rating)).filter(Review.rating.isnot(None))
    q = _apply_platform(q, Review, platform)
    value = q.scalar()
    return round(float(value), 2) if value is not None else None


def _empty_payload(platform: str, source: str = "empty_snapshot", status: str = "loading") -> dict[str, Any]:
    p = _normalize_platform(platform)
    return {
        "ok": True,
        "platform": p,
        "status": status,
        "generated_at": _now(),
        "source": source,
        "counts": {
            "reviews_total": 0,
            "questions_total": 0,
            "communications_total": 0,
            "reviews_unanswered": 0,
            "questions_unanswered": 0,
            "needs_response": 0,
            "ready_to_publish": 0,
            "high_risk": 0,
            "no_text_reviews": 0,
            "avg_rating": None,
            "products_total": None,
            "quality_attention": None,
            "operations_total": None,
            "operations_by_type": {},
        },
        "snapshot": {
            "available": False,
            "message": "Dashboard snapshot is not ready yet. Background refresh will fill it without blocking UI."
        },
        "marketplace_state": "not_connected" if p == "YM" else "loading",
    }


def build_live_snapshot_payload(db: Session, platform: str) -> dict[str, Any]:
    p = _normalize_platform(platform)

    if p == "YM":
        return _empty_payload("YM", source="dashboard_snapshot", status="not_connected")

    reviews_total = _count(db, Review, p)
    questions_total = _count(db, Question, p)
    reviews_unanswered = _count(db, Review, p, Review.operational_status == "needs_response")
    questions_unanswered = _count(db, Question, p, Question.operational_status == "needs_response")

    ready_statuses = ["ready_to_review", "ready_to_publish", "answer_rejected_quality_gate", "publish_dry_run"]
    ready_to_publish = (
        _count(db, Review, p, Review.status.in_(ready_statuses))
        + _count(db, Question, p, Question.status.in_(ready_statuses))
    )

    high_risk = (
        _count(db, Review, p, Review.ai_risk_level == "high")
        + _count(db, Question, p, Question.ai_risk_level == "high")
    )

    no_text_reviews = 0
    if p in {"ALL", "OZON"}:
        no_text_reviews = _count(db, Review, "OZON", Review.operational_status == "no_text_rating")

    return {
        "ok": True,
        "platform": p,
        "status": "ok",
        "generated_at": _now(),
        "source": "dashboard_snapshot",
        "counts": {
            "reviews_total": reviews_total,
            "questions_total": questions_total,
            "communications_total": reviews_total + questions_total,
            "reviews_unanswered": reviews_unanswered,
            "questions_unanswered": questions_unanswered,
            "needs_response": reviews_unanswered + questions_unanswered,
            "ready_to_publish": ready_to_publish,
            "high_risk": high_risk,
            "no_text_reviews": no_text_reviews,
            "avg_rating": _avg_rating(db, p),
            "products_total": None,
            "quality_attention": None,
            "operations_total": None,
            "operations_by_type": {},
        },
        "snapshot": {
            "available": True,
            "message": "Dashboard is served from cached DB snapshot. Heavy calculations are not in the UI critical path."
        },
        "marketplace_state": "connected" if p in {"WB", "OZON", "ALL"} else "not_connected",
    }


def save_dashboard_snapshot(db: Session, platform: str, payload: dict[str, Any]) -> None:
    p = _normalize_platform(platform)
    row = db.query(DashboardSnapshot).filter(DashboardSnapshot.platform == p).first()
    if not row:
        row = DashboardSnapshot(platform=p)
        db.add(row)
    row.payload = payload
    row.status = str(payload.get("status") or "ok")
    row.last_error = None
    row.generated_at = datetime.utcnow()


def refresh_dashboard_snapshots(db: Session) -> dict[str, Any]:
    results = {}
    for platform in PLATFORMS:
        try:
            payload = build_live_snapshot_payload(db, platform)
            save_dashboard_snapshot(db, platform, payload)
            results[platform] = {"status": "ok", "counts": payload.get("counts")}
        except Exception as exc:
            p = _normalize_platform(platform)
            row = db.query(DashboardSnapshot).filter(DashboardSnapshot.platform == p).first()
            if not row:
                row = DashboardSnapshot(platform=p, payload=_empty_payload(p, source="dashboard_snapshot_error", status="error"))
                db.add(row)
            row.status = "error"
            row.last_error = str(exc)
            results[p] = {"status": "error", "error": str(exc)}
    db.commit()
    return {"ok": True, "generated_at": _now(), "results": results}


def refresh_dashboard_snapshots_once() -> dict[str, Any]:
    db = SessionLocal()
    try:
        return refresh_dashboard_snapshots(db)
    finally:
        db.close()


def get_dashboard_snapshot(db: Session, platform: str | None = "ALL") -> dict[str, Any]:
    p = _normalize_platform(platform)
    try:
        row = db.query(DashboardSnapshot).filter(DashboardSnapshot.platform == p).first()
        if row and row.payload:
            payload = dict(row.payload)
            payload["platform"] = p
            payload["snapshot_meta"] = {
                "status": row.status,
                "generated_at": row.generated_at.isoformat() if row.generated_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "last_error": row.last_error,
            }
            return payload
    except Exception as exc:
        fallback = _empty_payload(p, source="dashboard_snapshot_read_error", status="error")
        fallback["error"] = str(exc)
        return fallback

    return _empty_payload(p)


async def start_dashboard_snapshot_loop() -> None:
    await asyncio.sleep(8)
    while True:
        try:
            await asyncio.to_thread(refresh_dashboard_snapshots_once)
        except Exception as exc:
            print(f"[dashboard_snapshot_loop] refresh failed: {exc}")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
