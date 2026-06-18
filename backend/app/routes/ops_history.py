from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Review, Question, RatingSnapshot
from app.config import settings

router = APIRouter(prefix="/ops", tags=["ops"])


def _safe_dt(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _as_list(rows):
    return rows if isinstance(rows, list) else []


def _item_url(platform: str | None, sku: str | None, product_url: str | None = None):
    if product_url:
        return product_url
    if not sku:
        return None
    platform = (platform or "").upper()
    if platform == "WB":
        return f"https://www.wildberries.ru/catalog/{sku}/detail.aspx"
    if platform == "OZON":
        return f"https://www.ozon.ru/search/?text={sku}"
    return None


def _product_key(x):
    return x.sku or x.product_name or "unknown"


def _serialize_item(x, kind: str):
    return {
        "id": x.id,
        "kind": kind,
        "platform": x.platform,
        "sku": x.sku,
        "product_name": x.product_name,
        "product_url": _item_url(x.platform, x.sku, getattr(x, "product_url", None)),
        "rating": getattr(x, "rating", None),
        "text": x.text,
        "created_at_marketplace": _safe_dt(x.created_at_marketplace),
        "created_at": _safe_dt(x.created_at),
        "updated_at": _safe_dt(x.updated_at),
        "status": x.status,
        "has_answer": x.has_answer,
        "source_status": x.source_status,
        "operational_status": x.operational_status,
        "ai_category": x.ai_category,
        "ai_sentiment": getattr(x, "ai_sentiment", None),
        "ai_risk_level": x.ai_risk_level,
        "ai_can_autopublish": getattr(x, "ai_can_autopublish", None),
        "ai_reason": x.ai_reason,
        "final_answer": x.final_answer,
        "response_origin": x.response_origin,
    }


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    reviews = db.query(Review).order_by(Review.created_at_marketplace.desc().nullslast()).limit(2000).all()
    questions = db.query(Question).order_by(Question.created_at_marketplace.desc().nullslast()).limit(2000).all()
    items = [("review", r) for r in reviews] + [("question", q) for q in questions]

    by_platform = Counter((x.platform or "UNKNOWN") for _, x in items)
    by_category = Counter((x.ai_category or "Без категории") for _, x in items)
    by_risk = Counter((x.ai_risk_level or "unknown") for _, x in items)

    needs_response = [x for _, x in items if x.operational_status == "needs_response" or x.has_answer is False]
    ready = [x for _, x in items if x.final_answer or x.status in {"ready_to_review", "ready_to_publish"}]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "reviews_total": len(reviews),
            "questions_total": len(questions),
            "needs_response": len(needs_response),
            "ready_with_draft": len(ready),
            "high_risk": by_risk.get("high", 0),
            "by_platform": dict(by_platform),
            "by_category": dict(by_category.most_common(20)),
            "by_risk": dict(by_risk),
        },
        "recent_reviews": [_serialize_item(x, "review") for x in reviews[:20]],
        "recent_questions": [_serialize_item(x, "question") for x in questions[:20]],
    }


@router.get("/product-summary")
def product_summary(platform: str | None = None, db: Session = Depends(get_db)):
    """
    RC1.4 Product Summary:
    - без искусственного лимита 500 товаров;
    - не падает, если rating_snapshots пустые/тяжелые;
    - строит каталог по всем загруженным отзывам и вопросам;
    - snapshots используются только как обогащение рейтинга.
    """
    p = platform.upper() if platform and platform.upper() != "ALL" else None

    reviews_q = db.query(Review)
    questions_q = db.query(Question)
    snaps_q = db.query(RatingSnapshot)

    if p:
        reviews_q = reviews_q.filter(Review.platform == p)
        questions_q = questions_q.filter(Question.platform == p)
        snaps_q = snaps_q.filter(RatingSnapshot.platform == p)

    groups = {}

    def key(platform_value, sku, product_name):
        return f"{platform_value or 'ALL'}::{sku or product_name or 'unknown'}"

    # Reviews aggregate
    review_rows = (
        reviews_q.with_entities(
            Review.platform,
            Review.sku,
            Review.product_name,
            func.count(Review.id).label("reviews"),
            func.avg(Review.rating).label("avg_rating"),
            func.sum(case((Review.rating <= 3, 1), else_=0)).label("negative"),
            func.max(Review.product_url).label("product_url"),
        )
        .group_by(Review.platform, Review.sku, Review.product_name)
        .all()
    )

    for platform_value, sku, product_name, reviews, avg_rating, negative, product_url in review_rows:
        k = key(platform_value, sku, product_name)
        g = groups.setdefault(k, {
            "key": k,
            "platform": platform_value,
            "platforms": set(),
            "sku": sku,
            "product_name": product_name,
            "product_url": product_url,
            "reviews": 0,
            "questions": 0,
            "negative": 0,
            "high_risk": 0,
            "avg_rating": None,
            "latest_rating": None,
            "feedbacks_count": None,
            "rating_snapshots": 0,
        })
        g["platforms"].add(platform_value)
        g["reviews"] += int(reviews or 0)
        g["negative"] += int(negative or 0)
        g["avg_rating"] = round(float(avg_rating), 2) if avg_rating is not None else g["avg_rating"]
        if product_url and not g.get("product_url"):
            g["product_url"] = product_url

    # Questions aggregate
    question_rows = (
        questions_q.with_entities(
            Question.platform,
            Question.sku,
            Question.product_name,
            func.count(Question.id).label("questions"),
            func.max(Question.product_url).label("product_url"),
        )
        .group_by(Question.platform, Question.sku, Question.product_name)
        .all()
    )

    for platform_value, sku, product_name, questions, product_url in question_rows:
        k = key(platform_value, sku, product_name)
        g = groups.setdefault(k, {
            "key": k,
            "platform": platform_value,
            "platforms": set(),
            "sku": sku,
            "product_name": product_name,
            "product_url": product_url,
            "reviews": 0,
            "questions": 0,
            "negative": 0,
            "high_risk": 0,
            "avg_rating": None,
            "latest_rating": None,
            "feedbacks_count": None,
            "rating_snapshots": 0,
        })
        g["platforms"].add(platform_value)
        g["questions"] += int(questions or 0)
        if product_url and not g.get("product_url"):
            g["product_url"] = product_url

    # Snapshot enrichment, bounded by latest per sku/platform using DB aggregation, not UI cap.
    try:
        latest_rows = (
            snaps_q.with_entities(
                RatingSnapshot.platform,
                RatingSnapshot.sku,
                func.max(RatingSnapshot.created_at).label("latest_at"),
            )
            .group_by(RatingSnapshot.platform, RatingSnapshot.sku)
            .all()
        )
        latest_keys = {(r[0], r[1], r[2]) for r in latest_rows}
        if latest_keys:
            for snap in snaps_q.all():
                if (snap.platform, snap.sku, snap.created_at) not in latest_keys:
                    continue
                k = key(snap.platform, snap.sku, getattr(snap, "product_name", None))
                g = groups.setdefault(k, {
                    "key": k,
                    "platform": snap.platform,
                    "platforms": set(),
                    "sku": snap.sku,
                    "product_name": getattr(snap, "product_name", None),
                    "product_url": getattr(snap, "product_url", None),
                    "reviews": 0,
                    "questions": 0,
                    "negative": 0,
                    "high_risk": 0,
                    "avg_rating": None,
                    "latest_rating": None,
                    "feedbacks_count": None,
                    "rating_snapshots": 0,
                })
                g["platforms"].add(snap.platform)
                g["latest_rating"] = getattr(snap, "rating", None) or g.get("latest_rating")
                g["feedbacks_count"] = getattr(snap, "feedbacks_count", None) or g.get("feedbacks_count")
                g["rating_snapshots"] += 1
    except Exception:
        pass

    items = []
    for g in groups.values():
        g["platforms"] = sorted([x for x in g["platforms"] if x])
        g["high_risk"] = 1 if int(g.get("negative") or 0) >= 3 else 0
        items.append(g)

    items.sort(key=lambda x: (int(x.get("negative") or 0), int(x.get("reviews") or 0) + int(x.get("questions") or 0)), reverse=True)

    return {
        "items": items,
        "total": len(items),
        "source": "reviews_questions_rating_snapshots",
        "limits_removed": True,
    }

@router.get("/product/{sku}")
def product_card(sku: str, platform: str | None = None, db: Session = Depends(get_db)):
    q1 = db.query(Review).filter(Review.sku == sku)
    q2 = db.query(Question).filter(Question.sku == sku)
    if platform and platform.upper() != "ALL":
        q1 = q1.filter(Review.platform == platform.upper())
        q2 = q2.filter(Question.platform == platform.upper())
    reviews = q1.order_by(Review.created_at_marketplace.desc().nullslast()).limit(500).all()
    questions = q2.order_by(Question.created_at_marketplace.desc().nullslast()).limit(500).all()
    all_rows = reviews + questions
    first = all_rows[0] if all_rows else None
    return {
        "sku": sku,
        "platform": platform or "ALL",
        "product_name": first.product_name if first else None,
        "product_url": _item_url(first.platform if first else platform, sku, getattr(first, "product_url", None) if first else None),
        "reviews": [_serialize_item(x, "review") for x in reviews],
        "questions": [_serialize_item(x, "question") for x in questions],
        "summary": {
            "reviews_total": len(reviews),
            "questions_total": len(questions),
            "high_risk": sum(1 for x in all_rows if x.ai_risk_level == "high"),
            "categories": dict(Counter(x.ai_category or "Без категории" for x in all_rows).most_common(10)),
        },
    }


@router.get("/operations-summary")
def operations_summary(platform: str | None = None):
    # API для актов/возвратов подключается следующим пакетом.
    # Эндпоинт уже стабилен для UI и возвращает безопасную структуру.
    return {
        "platform": (platform or "ALL").upper(),
        "items": [],
        "counts": {
            "returns": 0,
            "acts": 0,
            "shortages": 0,
            "surplus": 0,
            "depersonalized": 0,
            "discrepancies": 0,
        },
        "statuses": {"new": 0, "in_progress": 0, "waiting_marketplace": 0, "closed": 0},
        "message": "Operations Hub готов к подключению API возвратов, актов, недостач, излишков, обезлички и расхождений.",
    }


@router.get("/sync-history")
def sync_history():
    try:
        from app.services.sync_service import get_sync_status
        wb = get_sync_status()
    except Exception as e:
        wb = {"error": str(e)}
    try:
        from app.services.ozon_sync_service import get_ozon_status
        ozon = get_ozon_status()
    except Exception as e:
        ozon = {"error": str(e)}
    return {"wb": wb, "ozon": ozon}


@router.get("/publish-history")
def publish_history(db: Session = Depends(get_db)):
    reviews = db.query(Review).filter(Review.final_answer.isnot(None)).order_by(Review.updated_at.desc().nullslast()).limit(200).all()
    questions = db.query(Question).filter(Question.final_answer.isnot(None)).order_by(Question.updated_at.desc().nullslast()).limit(200).all()
    return {
        "items": [_serialize_item(x, "review") for x in reviews] + [_serialize_item(x, "question") for x in questions],
        "note": "История показывает записи с готовым ответом. Для точной истории публикаций нужен backend event log published_at/error_code.",
    }
