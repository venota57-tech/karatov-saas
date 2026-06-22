from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import MarketplaceOperation, Question, Review, SyncJob
from app.services.dashboard_service import build_dashboard


router = APIRouter(prefix="/marketplace-os", tags=["marketplace-os"])


def _platform(value: str | None) -> str:
    value = (value or "ALL").strip().upper()
    if value in {"WB", "WILDBERRIES", "WILDBERRY", "ВБ"}:
        return "WB"
    if value in {"OZON", "OZON.RU", "ОЗОН"}:
        return "OZON"
    if value in {"YM", "YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС"}:
        return "YM"
    return "ALL" if value in {"", "ALL"} else value


def _aliases(platform: str | None):
    p = _platform(platform)
    if p == "ALL":
        return None
    if p == "WB":
        return ["WB", "WILDBERRIES", "WILDBERRY", "ВБ"]
    if p == "OZON":
        return ["OZON", "OZON.RU", "ОЗОН"]
    if p == "YM":
        return ["YM", "YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС"]
    return [p]


def _filter_platform(q, model, platform: str | None):
    aliases = _aliases(platform)
    if aliases:
        return q.filter(model.platform.in_(aliases))
    return q


@router.get("/dashboard")
def marketplace_dashboard(platform: str = "ALL", db: Session = Depends(get_db)):
    p = _platform(platform)
    dashboard = build_dashboard(platform=p)
    latest_jobs = db.query(SyncJob).order_by(desc(SyncJob.created_at)).limit(10).all()
    return {"ok": True, "platform": p, "generated_at": datetime.now(timezone.utc).isoformat(), "dashboard": dashboard, "jobs": [{"id": j.id, "job_type": j.job_type, "platform": j.platform, "block": j.block, "status": j.status, "last_error": j.last_error, "created_at": j.created_at.isoformat() if j.created_at else None, "started_at": j.started_at.isoformat() if j.started_at else None, "finished_at": j.finished_at.isoformat() if j.finished_at else None} for j in latest_jobs]}


@router.get("/work-queue")
def work_queue(platform: str = "ALL", queue: str = "needs_response", limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    p = _platform(platform)
    safe_limit = min(max(int(limit or 100), 1), 500)
    safe_offset = max(int(offset or 0), 0)
    review_q = _filter_platform(db.query(Review), Review, p)
    question_q = _filter_platform(db.query(Question), Question, p)
    if queue == "needs_response":
        review_q = review_q.filter(Review.operational_status == "needs_response")
        question_q = question_q.filter(Question.operational_status == "needs_response")
    elif queue == "high_risk":
        review_q = review_q.filter(Review.ai_risk_level == "high")
        question_q = question_q.filter(Question.ai_risk_level == "high")
    elif queue == "answered":
        review_q = review_q.filter(Review.has_answer == True)  # noqa: E712
        question_q = question_q.filter(Question.has_answer == True)  # noqa: E712
    review_total = review_q.count()
    question_total = question_q.count()
    reviews = review_q.order_by(desc(Review.created_at_marketplace), desc(Review.id)).offset(safe_offset).limit(safe_limit).all()
    questions = question_q.order_by(desc(Question.created_at_marketplace), desc(Question.id)).offset(safe_offset).limit(safe_limit).all()
    items: list[dict[str, Any]] = []
    for r in reviews:
        items.append({"type": "review", "id": r.id, "platform": r.platform, "external_id": r.external_id, "sku": r.sku, "product_name": r.product_name, "rating": r.rating, "text": r.text, "pros": r.pros, "cons": r.cons, "has_answer": r.has_answer, "final_answer": r.final_answer, "draft_answer": r.draft_answer, "response_origin": r.response_origin, "answered_at": r.answered_at.isoformat() if r.answered_at else None, "ai_risk_level": r.ai_risk_level, "created_at_marketplace": r.created_at_marketplace.isoformat() if r.created_at_marketplace else None})
    for q in questions:
        items.append({"type": "question", "id": q.id, "platform": q.platform, "external_id": q.external_id, "sku": q.sku, "product_name": q.product_name, "text": q.text, "has_answer": q.has_answer, "final_answer": q.final_answer, "draft_answer": q.draft_answer, "response_origin": q.response_origin, "answered_at": q.answered_at.isoformat() if q.answered_at else None, "ai_risk_level": q.ai_risk_level, "created_at_marketplace": q.created_at_marketplace.isoformat() if q.created_at_marketplace else None})
    items.sort(key=lambda x: x.get("created_at_marketplace") or "", reverse=True)
    return {"ok": True, "platform": p, "queue": queue, "total": review_total + question_total, "review_total": review_total, "question_total": question_total, "limit": safe_limit, "offset": safe_offset, "items": items}


@router.get("/quality")
def quality(platform: str = "ALL", limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    p = _platform(platform)
    safe_limit = min(max(int(limit or 100), 1), 500)
    safe_offset = max(int(offset or 0), 0)
    rq = _filter_platform(db.query(Review), Review, p)
    qq = _filter_platform(db.query(Question), Question, p)
    high_risk = rq.filter(Review.ai_risk_level == "high").count() + qq.filter(Question.ai_risk_level == "high").count()
    no_text = _filter_platform(db.query(Review), Review, p).filter((Review.text == None) | (Review.text == "")).count()  # noqa: E711
    product_rows = _filter_platform(db.query(Review.platform, Review.sku, Review.product_name, func.count(Review.id).label("reviews"), func.avg(Review.rating).label("avg_rating")), Review, p).group_by(Review.platform, Review.sku, Review.product_name).order_by(desc(func.count(Review.id))).offset(safe_offset).limit(safe_limit).all()
    return {"ok": True, "platform": p, "total_attention": high_risk + no_text, "high_risk": high_risk, "no_text_reviews": no_text, "limit": safe_limit, "offset": safe_offset, "items": [{"platform": x.platform, "sku": x.sku, "product_name": x.product_name, "reviews": int(x.reviews or 0), "avg_rating": round(float(x.avg_rating), 2) if x.avg_rating is not None else None} for x in product_rows]}


@router.get("/operations")
def operations_summary(platform: str = "ALL", db: Session = Depends(get_db)):
    p = _platform(platform)
    q = _filter_platform(db.query(MarketplaceOperation), MarketplaceOperation, p)
    by_type = q.with_entities(MarketplaceOperation.operation_type, func.count(MarketplaceOperation.id)).group_by(MarketplaceOperation.operation_type).all()
    return {"ok": True, "platform": p, "status": "connected" if p in {"ALL", "WB", "OZON"} else "not_connected", "total": q.count(), "by_type": {str(k or "unknown"): int(v or 0) for k, v in by_type}, "supported_types": ["returns", "acts", "shortages", "surpluses", "anonymization", "discrepancies"], "note": "If a marketplace API block is unavailable, worker jobs must write permission_error/not_supported_yet rather than returning fake zeros."}
