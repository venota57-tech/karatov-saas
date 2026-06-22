from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Review, Question

READY_STATUSES = {
    "ready_to_review",
    "ready_to_publish",
    "answer_rejected_quality_gate",
    "publish_dry_run",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_platform(value: str | None) -> str:
    if not value:
        return "ALL"
    value = str(value).upper()
    if value in {"ALL", "WB", "OZON", "YM"}:
        return value
    if value in {"WILDBERRIES", "WILDBERRY", "ВБ"}:
        return "WB"
    if value in {"OZON.RU", "ОЗОН"}:
        return "OZON"
    if value in {"YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС"}:
        return "YM"
    return value


def _platform_aliases(platform: str) -> list[str]:
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
    aliases = _platform_aliases(platform)
    if not aliases:
        return q
    upper_aliases = [x.upper() for x in aliases]
    return q.filter(func.upper(model.platform).in_(upper_aliases))


def _count(db: Session, model: Any, platform: str, *filters: Any) -> int:
    try:
        q = db.query(func.count(model.id))
        q = _apply_platform(q, model, platform)
        for f in filters:
            q = q.filter(f)
        return int(q.scalar() or 0)
    except Exception:
        return 0


def _avg_rating(db: Session, platform: str) -> float | None:
    try:
        q = db.query(func.avg(Review.rating)).filter(Review.rating.isnot(None))
        q = _apply_platform(q, Review, platform)
        value = q.scalar()
        return round(float(value), 2) if value is not None else None
    except Exception:
        return None


def build_dashboard(db: Session, platform: str | None = "ALL") -> dict[str, Any]:
    p = _normalize_platform(platform)

    reviews_total = _count(db, Review, p)
    questions_total = _count(db, Question, p)

    reviews_unanswered = _count(db, Review, p, Review.operational_status == "needs_response")
    questions_unanswered = _count(db, Question, p, Question.operational_status == "needs_response")

    ready_reviews = _count(db, Review, p, Review.status.in_(list(READY_STATUSES)))
    ready_questions = _count(db, Question, p, Question.status.in_(list(READY_STATUSES)))

    high_risk_reviews = _count(db, Review, p, Review.ai_risk_level == "high")
    high_risk_questions = _count(db, Question, p, Question.ai_risk_level == "high")

    no_text_reviews = 0
    if p in {"ALL", "OZON"}:
        no_text_reviews = _count(
            db,
            Review,
            "OZON" if p == "ALL" else p,
            or_(Review.text.is_(None), Review.text == ""),
            or_(Review.pros.is_(None), Review.pros == ""),
            or_(Review.cons.is_(None), Review.cons == ""),
        )

    counts = {
        "reviews_total": reviews_total,
        "questions_total": questions_total,
        "communications_total": reviews_total + questions_total,
        "reviews_unanswered": reviews_unanswered,
        "questions_unanswered": questions_unanswered,
        "needs_response": reviews_unanswered + questions_unanswered,
        "ready_to_publish": ready_reviews + ready_questions,
        "high_risk": high_risk_reviews + high_risk_questions,
        "no_text_reviews": no_text_reviews,
        "avg_rating": _avg_rating(db, p),
        "products_total": None,
        "quality_attention": None,
        "operations_total": None,
        "operations_by_type": {},
    }

    return {
        "ok": True,
        "platform": p,
        "generated_at": _now(),
        "counts": counts,
        "source": "server_dashboard_lightweight",
        "note": "Product Summary, Quality Hub and Operations totals are loaded by their own endpoints and must not block dashboard startup.",
    }
