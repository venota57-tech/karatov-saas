from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Review, Question

router = APIRouter(prefix="/marketplace-health", tags=["marketplace-health"])


def _safe(fn):
    try:
        return fn()
    except Exception as exc:
        return {"error": str(exc)}


def _counts(db: Session, platform: str):
    return {
        "reviews_total": db.query(Review).filter(Review.platform == platform).count(),
        "questions_total": db.query(Question).filter(Question.platform == platform).count(),
        "reviews_needs_response": db.query(Review).filter(Review.platform == platform, Review.operational_status == "needs_response").count(),
        "questions_needs_response": db.query(Question).filter(Question.platform == platform, Question.operational_status == "needs_response").count(),
        "reviews_answered_from_cabinet": db.query(Review).filter(Review.platform == platform, Review.response_origin == "seller_cabinet").count(),
        "questions_answered_from_cabinet": db.query(Question).filter(Question.platform == platform, Question.response_origin == "seller_cabinet").count(),
        "reviews_with_answer": db.query(Review).filter(Review.platform == platform, Review.has_answer == True).count(),  # noqa: E712
        "questions_with_answer": db.query(Question).filter(Question.platform == platform, Question.has_answer == True).count(),  # noqa: E712
    }


@router.get("")
def health(db: Session = Depends(get_db)):
    from app.services.sync_service import get_sync_status
    from app.services.ozon_sync_service import get_ozon_status

    wb_status = _safe(get_sync_status)
    ozon_status = _safe(get_ozon_status)
    return {
        "WB": {"counts": _counts(db, "WB"), "sync": wb_status},
        "OZON": {"counts": _counts(db, "OZON"), "sync": ozon_status},
    }


@router.get("/wb/questions-probe")
async def wb_questions_probe_endpoint():
    from app.services.sync_service import wb_questions_probe
    return await wb_questions_probe()
