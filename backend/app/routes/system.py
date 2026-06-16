from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..config import settings
from ..database import get_db
from ..models import Review, Question
from ..services.automation_rules import get_rules

try:
    from ..services.sync_service import get_sync_status
except Exception:  # noqa: BLE001
    get_sync_status = None

try:
    from ..services.ozon_sync_service import get_ozon_status
except Exception:  # noqa: BLE001
    get_ozon_status = None

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db)):
    rules = get_rules(db).rules or {}
    counts = {
        "reviews_total": db.query(func.count(Review.id)).scalar() or 0,
        "questions_total": db.query(func.count(Question.id)).scalar() or 0,
        "reviews_unanswered": db.query(func.count(Review.id)).filter(Review.operational_status == "needs_response").scalar() or 0,
        "questions_unanswered": db.query(func.count(Question.id)).filter(Question.operational_status == "needs_response").scalar() or 0,
        "ready_to_publish": (
            (db.query(func.count(Review.id)).filter(Review.status == "ready_to_publish").scalar() or 0)
            + (db.query(func.count(Question.id)).filter(Question.status == "ready_to_publish").scalar() or 0)
        ),
        "high_risk": (
            (db.query(func.count(Review.id)).filter(Review.ai_risk_level == "high").scalar() or 0)
            + (db.query(func.count(Question.id)).filter(Question.ai_risk_level == "high").scalar() or 0)
        ),
    }
    return {
        "status": "ok",
        "keys": {
            "openai_api_key": bool(settings.openai_api_key),
            "wb_api_token": bool(settings.wb_api_token),
            "ozon_client_id": bool(settings.ozon_client_id),
            "ozon_api_key": bool(settings.ozon_api_key),
        },
        "openai": {
            "model": settings.openai_model,
            "ai_generation_enabled": bool(rules.get("ai_generation_enabled", True)),
            "fallback_to_local_templates": bool(rules.get("ai_fallback_to_local_templates", True)),
        },
        "publishing": {
            "enable_marketplace_publishing": bool(settings.enable_marketplace_publishing),
            "mode": "real_publish" if settings.enable_marketplace_publishing else "dry_run",
        },
        "counts": counts,
        "rules": rules,
        "wb_sync": get_sync_status() if get_sync_status else None,
        "ozon_sync": get_ozon_status() if get_ozon_status else None,
    }
