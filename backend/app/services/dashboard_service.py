
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
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


def _platform(value: str | None) -> str | None:
    if not value or str(value).upper() == "ALL":
        return None
    return str(value).upper()


def _platform_values(platform: str | None) -> list[str]:
    if not platform:
        return []
    p = str(platform).upper()
    if p == "WB":
        return ["WB", "WILDBERRIES", "WILDBERRY", "ВБ"]
    if p == "OZON":
        return ["OZON", "OZON.RU", "ОЗОН"]
    if p in {"YM", "YANDEX", "YANDEX_MARKET", "ЯМ"}:
        return ["YM", "YANDEX", "YANDEX_MARKET", "ЯНДЕКС", "ЯМ"]
    return [p]


def _platform_filter(model: Any, platform: str | None):
    return func.upper(model.platform).in_(_platform_values(platform))


def _safe_count(db: Session, model: Any, *filters: Any) -> int:
    try:
        q = db.query(func.count(model.id))
        for f in filters:
            q = q.filter(f)
        return int(q.scalar() or 0)
    except Exception:
        return 0


def _base_query(db: Session, model: Any, platform: str | None):
    q = db.query(model)
    if platform:
        q = q.filter(_platform_filter(model, platform))
    return q


def _count_products(db: Session, platform: str | None) -> int:
    keys: set[tuple[str, str]] = set()
    try:
        for model in (Review, Question):
            q = db.query(model.platform, model.sku)
            if platform:
                q = q.filter(_platform_filter(model, platform))
            q = q.filter(model.sku.isnot(None))
            for p, sku in q.all():
                if sku:
                    keys.add((str(p or ""), str(sku)))
    except Exception:
        return 0
    return len(keys)


def _count_quality_attention(db: Session, platform: str | None) -> int:
    keys: set[tuple[str, str]] = set()
    try:
        rq = db.query(Review.platform, Review.sku).filter(Review.sku.isnot(None))
        if platform:
            rq = rq.filter(_platform_filter(Review, platform))
        rq = rq.filter((Review.ai_risk_level == "high") | (Review.rating <= 3))
        for p, sku in rq.all():
            if sku:
                keys.add((str(p or ""), str(sku)))

        qq = db.query(Question.platform, Question.sku).filter(Question.sku.isnot(None))
        if platform:
            qq = qq.filter(_platform_filter(Question, platform))
        qq = qq.filter(Question.ai_risk_level == "high")
        for p, sku in qq.all():
            if sku:
                keys.add((str(p or ""), str(sku)))
    except Exception:
        return 0
    return len(keys)


def _avg_rating(db: Session, platform: str | None) -> float | None:
    try:
        q = db.query(func.avg(Review.rating)).filter(Review.rating.isnot(None))
        if platform:
            q = q.filter(_platform_filter(Review, platform))
        value = q.scalar()
        return round(float(value), 2) if value is not None else None
    except Exception:
        return None


def _operations_counts(db: Session, platform: str | None) -> tuple[int, dict[str, int]]:
    try:
        from app.models import MarketplaceOperation
    except Exception:
        return 0, {}

    try:
        q = db.query(MarketplaceOperation)
        if platform:
            q = q.filter(_platform_filter(MarketplaceOperation, platform))
        total = q.with_entities(func.count(MarketplaceOperation.id)).scalar() or 0
        rows = (
            q.with_entities(MarketplaceOperation.operation_type, func.count(MarketplaceOperation.id))
            .group_by(MarketplaceOperation.operation_type)
            .all()
        )
        return int(total or 0), {str(k or "unknown"): int(v or 0) for k, v in rows}
    except Exception:
        return 0, {}


def build_dashboard(db: Session, platform: str | None = "ALL") -> dict[str, Any]:
    p = _platform(platform)

    review_q = _base_query(db, Review, p)
    question_q = _base_query(db, Question, p)

    reviews_total = int(review_q.with_entities(func.count(Review.id)).scalar() or 0)
    questions_total = int(question_q.with_entities(func.count(Question.id)).scalar() or 0)

    reviews_unanswered = int(
        review_q.filter(Review.operational_status == "needs_response")
        .with_entities(func.count(Review.id))
        .scalar()
        or 0
    )
    questions_unanswered = int(
        question_q.filter(Question.operational_status == "needs_response")
        .with_entities(func.count(Question.id))
        .scalar()
        or 0
    )

    ready_reviews = int(
        review_q.filter(Review.status.in_(list(READY_STATUSES)))
        .with_entities(func.count(Review.id))
        .scalar()
        or 0
    )
    ready_questions = int(
        question_q.filter(Question.status.in_(list(READY_STATUSES)))
        .with_entities(func.count(Question.id))
        .scalar()
        or 0
    )

    high_risk_reviews = int(
        review_q.filter(Review.ai_risk_level == "high")
        .with_entities(func.count(Review.id))
        .scalar()
        or 0
    )
    high_risk_questions = int(
        question_q.filter(Question.ai_risk_level == "high")
        .with_entities(func.count(Question.id))
        .scalar()
        or 0
    )

    no_text_reviews = int(
        review_q.filter(
            _platform_filter(Review, "OZON"),
            (Review.text.is_(None) | (Review.text == "")),
            (Review.pros.is_(None) | (Review.pros == "")),
            (Review.cons.is_(None) | (Review.cons == "")),
        )
        .with_entities(func.count(Review.id))
        .scalar()
        or 0
    )

    operations_total, operations_by_type = _operations_counts(db, p)

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
        "products_total": _count_products(db, p),
        "quality_attention": _count_quality_attention(db, p),
        "operations_total": operations_total,
        "operations_by_type": operations_by_type,
    }

    return {
        "ok": True,
        "platform": p or "ALL",
        "generated_at": _now(),
        "counts": counts,
        "source": "server_dashboard",
    }
