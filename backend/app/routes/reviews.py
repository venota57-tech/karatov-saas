from __future__ import annotations

import random
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, and_, not_

from ..database import get_db
from ..models import Review
from ..schemas import ReviewOut, AnswerUpdate
from ..ai.answer_generator import AnswerGenerator
from ..services.automation_rules import apply_publication_rules, get_rules
from ..services.publishing_service import publish_review, publish_reviews_bulk, edit_published_review_answer

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _ozon_no_text_condition():
    empty_text = and_(Review.text.is_(None) | (Review.text == ''), Review.pros.is_(None) | (Review.pros == ''), Review.cons.is_(None) | (Review.cons == ''))
    return and_(Review.platform == 'OZON', empty_text)

def _is_ozon_no_text(review: Review) -> bool:
    return bool(getattr(review, 'no_text_rating', False))


@router.get("", response_model=list[ReviewOut])
def list_reviews(
    status: str | None = None,
    platform: str | None = None,
    answer_state: str = "all",
    source_status: str | None = None,
    product: str | None = None,
    category: str | None = None,
    risk: str | None = None,
    response_origin: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    q = db.query(Review)
    if status:
        q = q.filter(Review.status == status)
    if platform:
        q = q.filter(Review.platform == platform)
    if source_status:
        q = q.filter(Review.source_status == source_status)
    if product:
        like = f"%{product}%"
        q = q.filter(or_(Review.sku == product, Review.product_name.ilike(like), Review.external_id == product))
    if category:
        q = q.filter(Review.ai_category == category)
    if risk:
        q = q.filter(Review.ai_risk_level == risk)
    if response_origin:
        q = q.filter(Review.response_origin == response_origin)
    if answer_state == "answered":
        q = q.filter(Review.source_status.in_(["wb_answered", "wb_archive", "ozon_answered"]))
    elif answer_state == "unanswered":
        q = q.filter(Review.operational_status == "needs_response", Review.source_status.in_(["wb_unanswered", "ozon_unanswered"]))
        q = q.filter(not_(_ozon_no_text_condition()))
    elif answer_state == "no_text_rating":
        q = q.filter(_ozon_no_text_condition())
    elif answer_state == "stale":
        q = q.filter(Review.operational_status == "stale_unanswered")
    elif answer_state == "manual":
        q = q.filter(Review.status.in_(["ready_to_review", "ready_to_publish", "answer_rejected_quality_gate", "publish_dry_run"]))
    elif answer_state == "auto_published":
        q = q.filter(Review.status.in_(["auto_published", "published"]))
    return q.order_by(desc(Review.created_at_marketplace), desc(Review.created_at)).limit(min(max(limit, 1), 1000)).all()


@router.post("/{review_id}/generate", response_model=ReviewOut)
def generate_review_answer(review_id: int, db: Session = Depends(get_db)):
    review = db.get(Review, review_id)
    if not review:
        raise HTTPException(404, "Отзыв не найден")
    if _is_ozon_no_text(review):
        review.status = "no_text_rating"
        review.operational_status = "analytics_only"
        review.ai_can_autopublish = False
        review.publish_blocked_reason = "Ozon не позволяет отвечать на оценки без текста. AI и шаблоны не используются."
        db.commit()
        db.refresh(review)
        raise HTTPException(400, "Ozon не позволяет отвечать на оценки без текста. Отзыв перенесен в аналитику без SLA.")
    rules = get_rules(db).rules or {}
    result = AnswerGenerator(rules).generate_for_review_until_pass({
        "platform": review.platform,
        "sku": review.sku,
        "product_name": review.product_name,
        "rating": review.rating,
        "text": review.text,
        "pros": review.pros,
        "cons": review.cons,
        "client_name": review.client_name,
        "variation_seed": random.randint(1, 1_000_000),
    })
    result = apply_publication_rules(result, "review", review.rating, db)
    review.ai_category = result.get("category")
    review.ai_sentiment = result.get("sentiment")
    review.ai_risk_level = result.get("risk_level")
    review.ai_can_autopublish = bool(result.get("can_autopublish"))
    review.ai_reason = result.get("reason")
    review.ai_tags = result.get("tags") or review.ai_tags
    review.draft_answer = result.get("answer_text") or None
    review.final_answer = result.get("answer_text") or None
    if not result.get("answer_text"):
        review.status = "answer_rejected_quality_gate"
        review.publish_blocked_reason = result.get("reason") or "Ответ не прошел quality gate 10/10"
    else:
        review.status = "ready_to_review" if review.operational_status == "needs_response" else "local_draft"
        review.publish_blocked_reason = None if review.operational_status == "needs_response" else review.publish_blocked_reason
    db.commit()
    db.refresh(review)
    return review


@router.patch("/{review_id}/answer", response_model=ReviewOut)
def update_review_answer(review_id: int, payload: AnswerUpdate, db: Session = Depends(get_db)):
    review = db.get(Review, review_id)
    if not review:
        raise HTTPException(404, "Отзыв не найден")
    if _is_ozon_no_text(review):
        raise HTTPException(400, "На Ozon нельзя отвечать на оценку без текста")
    review.final_answer = payload.final_answer
    review.draft_answer = payload.final_answer
    review.status = "ready_to_publish" if review.operational_status == "needs_response" else "local_edited"
    db.commit()
    db.refresh(review)
    return review


@router.post("/{review_id}/publish")
async def publish(review_id: int, db: Session = Depends(get_db)):
    review = db.get(Review, review_id)
    if review and _is_ozon_no_text(review):
        raise HTTPException(400, "На Ozon нельзя публиковать ответ на оценку без текста")
    try:
        return await publish_review(db, review_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@router.post("/{review_id}/edit-published")
async def edit_published(review_id: int, db: Session = Depends(get_db)):
    try:
        return await edit_published_review_answer(db, review_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@router.post("/bulk-publish")
async def bulk_publish(payload: dict, db: Session = Depends(get_db)):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "Передай список ids для публикации")
    try:
        return await publish_reviews_bulk(db, ids)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))
