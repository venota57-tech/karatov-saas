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
def product_summary(platform: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q1 = db.query(Review)
    q2 = db.query(Question)
    if platform and platform.upper() != "ALL":
        q1 = q1.filter(Review.platform == platform.upper())
        q2 = q2.filter(Question.platform == platform.upper())

    reviews = q1.all()
    questions = q2.all()
    snap_q = db.query(RatingSnapshot)
    if platform and platform.upper() != "ALL":
        snap_q = snap_q.filter(RatingSnapshot.platform == platform.upper())
    snapshots = snap_q.order_by(RatingSnapshot.created_at.desc()).limit(5000).all()
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "sku": None,
        "product_name": None,
        "platforms": set(),
        "reviews": 0,
        "questions": 0,
        "rating_sum": 0,
        "rating_count": 0,
        "negative": 0,
        "high_risk": 0,
        "categories": Counter(),
        "latest_at": None,
        "product_url": None,
        "samples": [],
        "rating_snapshots": 0,
        "latest_rating": None,
        "feedbacks_count": None,
    })

    def add_snapshot(x):
        key = x.sku or x.product_name or "unknown"
        g = grouped[key]
        g["sku"] = x.sku or g["sku"]
        g["product_name"] = x.product_name or g["product_name"]
        g["platforms"].add(x.platform or "UNKNOWN")
        g["rating_snapshots"] += 1
        g["latest_rating"] = x.rating or g["latest_rating"]
        g["feedbacks_count"] = x.feedbacks_count if x.feedbacks_count is not None else g["feedbacks_count"]
        dtv = x.created_at
        if dtv and (g["latest_at"] is None or dtv > g["latest_at"]):
            g["latest_at"] = dtv
        url = _item_url(x.platform, x.sku, getattr(x, "product_url", None))
        if url:
            g["product_url"] = url

    def add(x, kind):
        key = _product_key(x)
        g = grouped[key]
        g["sku"] = x.sku or g["sku"]
        g["product_name"] = x.product_name or g["product_name"]
        g["platforms"].add(x.platform or "UNKNOWN")
        g[kind + "s"] += 1
        if getattr(x, "rating", None):
            g["rating_sum"] += x.rating
            g["rating_count"] += 1
            if x.rating <= 3:
                g["negative"] += 1
        if x.ai_sentiment == "negative":
            g["negative"] += 1
        if x.ai_risk_level == "high":
            g["high_risk"] += 1
        if x.ai_category:
            g["categories"][x.ai_category] += 1
        dtv = x.created_at_marketplace or x.created_at
        if dtv and (g["latest_at"] is None or dtv > g["latest_at"]):
            g["latest_at"] = dtv
        url = _item_url(x.platform, x.sku, getattr(x, "product_url", None))
        if url:
            g["product_url"] = url
        if len(g["samples"]) < 3 and (x.text or getattr(x, "pros", None) or getattr(x, "cons", None)):
            g["samples"].append(x.text or getattr(x, "pros", None) or getattr(x, "cons", None))

    for r in reviews:
        add(r, "review")
    for q in questions:
        add(q, "question")
    for sn in snapshots:
        add_snapshot(sn)

    rows = []
    for key, g in grouped.items():
        total = g["reviews"] + g["questions"]
        avg_rating = round(g["rating_sum"] / g["rating_count"], 2) if g["rating_count"] else None
        risk_score = g["high_risk"] * 5 + g["negative"] * 2 + total * 0.05
        top_categories = g["categories"].most_common(5)
        rows.append({
            "key": key,
            "sku": g["sku"],
            "product_name": g["product_name"],
            "platforms": sorted(g["platforms"]),
            "product_url": g["product_url"],
            "reviews": g["reviews"],
            "questions": g["questions"],
            "avg_rating": avg_rating,
            "latest_rating": g.get("latest_rating"),
            "feedbacks_count": g.get("feedbacks_count"),
            "rating_snapshots": g.get("rating_snapshots", 0),
            "negative": g["negative"],
            "high_risk": g["high_risk"],
            "risk_score": round(risk_score, 2),
            "top_categories": top_categories,
            "latest_at": _safe_dt(g["latest_at"]),
            "ai_summary": _build_product_summary_text(g, avg_rating, top_categories),
            "recommendation": _build_product_recommendation(g, top_categories),
            "samples": g["samples"],
        })

    rows.sort(key=lambda x: (x["risk_score"], x["reviews"] + x["questions"]), reverse=True)
    return {"items": rows[: min(limit, 500)], "total_products": len(rows)}


def _build_product_summary_text(g, avg_rating, cats):
    main_cat = cats[0][0] if cats else "нет выраженной темы"
    rating_text = avg_rating if avg_rating is not None else (g.get("latest_rating") or "нет данных")
    return (
        f"По товару собрано {g['reviews']} отзывов и {g['questions']} вопросов. "
        f"Рейтинг: {rating_text}. Отзывов на карточке: {g.get('feedbacks_count') or 'нет данных'}. "
        f"Основная тема: {main_cat}. Высоких рисков: {g['high_risk']}."
    )


def _build_product_recommendation(g, cats):
    if g["high_risk"]:
        return "Проверить товар в приоритете: есть высокорисковые отзывы/темы, нужна ручная проверка ответа и передача в качество."
    if cats:
        return f"Проверить повторяемость темы «{cats[0][0]}» и при необходимости обновить карточку/описание или передать замечание технологам."
    return "Критичных действий не требуется; продолжать мониторинг динамики отзывов и вопросов."


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
