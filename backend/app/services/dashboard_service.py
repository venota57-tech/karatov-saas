from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Review, Question


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform(value: str | None) -> str:
    value = (value or "ALL").upper()
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
    if platform == "ALL":
        return []
    if platform == "WB":
        return ["WB", "WILDBERRIES", "WILDBERRY", "ВБ", "wb", "wildberries"]
    if platform == "OZON":
        return ["OZON", "OZON.RU", "ОЗОН", "ozon"]
    if platform == "YM":
        return ["YM", "YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС", "ym", "yandex"]
    return [platform]


def _safe_count(db: Session, model: Any, platform: str) -> int:
    try:
        q = db.query(func.count(model.id))
        aliases = _aliases(platform)
        if aliases:
            q = q.filter(func.upper(model.platform).in_([x.upper() for x in aliases]))
        return int(q.scalar() or 0)
    except Exception:
        return 0


def build_dashboard(db: Session, platform: str | None = "ALL") -> dict[str, Any]:
    p = _platform(platform)

    reviews_total = _safe_count(db, Review, p)
    questions_total = _safe_count(db, Question, p)

    return {
        "ok": True,
        "platform": p,
        "generated_at": _now(),
        "source": "emergency_light_dashboard",
        "counts": {
            "reviews_total": reviews_total,
            "questions_total": questions_total,
            "communications_total": reviews_total + questions_total,
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
        "note": "Emergency lightweight dashboard: only total review/question counters are loaded here. Detailed SLA, Quality Hub, Product Summary and Operations must load from separate endpoints.",
    }


def build_dashboard_snapshot(db: Session, platform: str | None = "ALL") -> dict[str, Any]:
    return build_dashboard(db, platform)
