from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from ..config import settings
from ..database import get_db, run_lightweight_migrations, engine
from ..models import Review, Question
from ..services.automation_rules import get_rules

try:
    from ..services.sync_service import get_sync_status
except Exception:
    get_sync_status = None

try:
    from ..services.ozon_sync_service import get_ozon_status
except Exception:
    get_ozon_status = None


router = APIRouter(prefix="/system", tags=["system"])


def _has_column(table: str, column: str) -> bool:
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            row = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = :table
                      AND column_name = :column
                    LIMIT 1
                    """
                ),
                {"table": table, "column": column},
            ).fetchone()
            return row is not None

        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(row[1] == column for row in rows)


def _safe_count(db: Session, model, condition=None) -> int:
    try:
        q = db.query(func.count(model.id))
        if condition is not None:
            q = q.filter(condition)
        return q.scalar() or 0
    except Exception:
        db.rollback()
        return 0


@router.get("/migrate")
def migrate():
    return run_lightweight_migrations()


@router.post("/migrate")
def migrate_post():
    return run_lightweight_migrations()


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db)):
    migration = run_lightweight_migrations()

    rules = get_rules(db).rules or {}

    reviews_has_ai_risk_level = _has_column("reviews", "ai_risk_level")
    questions_has_ai_risk_level = _has_column("questions", "ai_risk_level")

    high_risk = 0
    if reviews_has_ai_risk_level:
        high_risk += _safe_count(db, Review, Review.ai_risk_level == "high")
    if questions_has_ai_risk_level:
        high_risk += _safe_count(db, Question, Question.ai_risk_level == "high")

    counts = {
        "reviews_total": _safe_count(db, Review),
        "questions_total": _safe_count(db, Question),
        "reviews_unanswered": _safe_count(db, Review, Review.operational_status == "needs_response"),
        "questions_unanswered": _safe_count(db, Question, Question.operational_status == "needs_response"),
        "ready_to_publish": (
            _safe_count(db, Review, Review.status == "ready_to_publish")
            + _safe_count(db, Question, Question.status == "ready_to_publish")
        ),
        "high_risk": high_risk,
    }

    return {
        "status": "ok",
        "migration": migration,
        "db_schema": {
            "reviews_has_ai_risk_level": reviews_has_ai_risk_level,
            "questions_has_ai_risk_level": questions_has_ai_risk_level,
        },
        "keys": {
            "openai_api_key": bool(settings.openai_api_key),
            "wb_api_key": bool(settings.wb_api_token),
            "wb_api_token": bool(settings.wb_api_token),
            "ozon_client_id": bool(settings.ozon_client_id),
            "ozon_api_key": bool(settings.ozon_api_key),
        },
        "env_names": {
            "wb_supported": ["WB_API_KEY", "WB_API_TOKEN"],
            "ozon_required": ["OZON_CLIENT_ID", "OZON_API_KEY"],
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