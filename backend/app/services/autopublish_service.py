from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Review, Question
from app.services.automation_rules import get_rules

_status: dict[str, Any] = {
    "enabled": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_result": None,
    "last_error": None,
    "mode": "dry_run",
    "queue_policy": "separate_from_sync",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _matrix_enabled(rules: dict, platform: str, kind: str) -> bool:
    matrix = rules.get("autopublish_matrix") or {}
    return bool((matrix.get(platform.upper()) or {}).get(kind))


def _can_publish_review(review: Review, rules: dict) -> tuple[bool, str]:
    if not settings.enable_marketplace_publishing:
        return False, "ENABLE_MARKETPLACE_PUBLISHING=false: режим безопасной синхронизации без публикации"
    if not bool(rules.get("real_autopublish_enabled", False)):
        return False, "real_autopublish_enabled=false"
    if not _matrix_enabled(rules, review.platform or "", "reviews"):
        return False, f"Матрица автопубликации отключена для {review.platform} reviews"
    if not review.final_answer:
        return False, "Нет final_answer"
    min_rating = int(rules.get("positive_review_min_rating") or 5)
    if review.rating is not None and review.rating < min_rating:
        return False, f"Оценка {review.rating} ниже минимальной {min_rating}"
    if review.ai_risk_level in set(rules.get("require_review_risk_levels") or ["medium", "high"]):
        return False, f"Риск {review.ai_risk_level} требует ручной проверки"
    if review.ai_category in set(rules.get("require_review_categories") or []):
        return False, f"Категория {review.ai_category} требует ручной проверки"
    if review.has_answer:
        return False, "Уже есть ответ"
    return True, "ok"


def _can_publish_question(q: Question, rules: dict) -> tuple[bool, str]:
    if not settings.enable_marketplace_publishing:
        return False, "ENABLE_MARKETPLACE_PUBLISHING=false: режим безопасной синхронизации без публикации"
    if not bool(rules.get("real_autopublish_enabled", False)):
        return False, "real_autopublish_enabled=false"
    if not _matrix_enabled(rules, q.platform or "", "questions"):
        return False, f"Матрица автопубликации отключена для {q.platform} questions"
    if not q.final_answer:
        return False, "Нет final_answer"
    if q.ai_risk_level in {"medium", "high"}:
        return False, f"Риск {q.ai_risk_level} требует ручной проверки"
    if q.has_answer:
        return False, "Уже есть ответ"
    return True, "ok"


async def autopublish_once(db: Session | None = None) -> dict[str, Any]:
    own_db = db is None
    db = db or SessionLocal()
    _status["last_started_at"] = _now()
    _status["mode"] = "real_publish" if settings.enable_marketplace_publishing else "dry_run"
    try:
        rules = get_rules(db).rules or {}
        max_per_run = int(rules.get("autopublish_max_per_run") or 10)
        pause = max(1, int(rules.get("autopublish_pause_between_items_seconds") or 30))

        result = {"checked": 0, "generated": 0, "published": 0, "skipped": 0, "errors": [], "mode": _status["mode"]}

        reviews = (
            db.query(Review)
            .filter(Review.operational_status == "needs_response")
            .filter(Review.final_answer.isnot(None))
            .order_by(Review.created_at_marketplace.asc().nullslast())
            .limit(max_per_run)
            .all()
        )
        questions = (
            db.query(Question)
            .filter(Question.operational_status == "needs_response")
            .filter(Question.final_answer.isnot(None))
            .order_by(Question.created_at_marketplace.asc().nullslast())
            .limit(max_per_run)
            .all()
        )

        # Safety: if publishing disabled, only explain why and never call WB/Ozon.
        for item in reviews:
            result["checked"] += 1
            ok, reason = _can_publish_review(item, rules)
            if not ok:
                item.publish_blocked_reason = reason
                result["skipped"] += 1
                continue
            try:
                await _publish_review(item)
                item.status = "auto_published"
                item.response_origin = "auto_app"
                item.has_answer = True
                result["published"] += 1
                await asyncio.sleep(pause)
            except Exception as exc:
                item.publish_blocked_reason = str(exc)
                result["errors"].append(f"review {item.id}: {exc}")
                # После 429 не добиваем API — завершаем проход.
                if "429" in str(exc):
                    break

        for item in questions:
            if result["published"] >= max_per_run:
                break
            result["checked"] += 1
            ok, reason = _can_publish_question(item, rules)
            if not ok:
                item.publish_blocked_reason = reason
                result["skipped"] += 1
                continue
            try:
                await _publish_question(item)
                item.status = "auto_published"
                item.response_origin = "auto_app"
                item.has_answer = True
                result["published"] += 1
                await asyncio.sleep(pause)
            except Exception as exc:
                item.publish_blocked_reason = str(exc)
                result["errors"].append(f"question {item.id}: {exc}")
                if "429" in str(exc):
                    break

        db.commit()
        _status["last_result"] = result
        _status["last_finished_at"] = _now()
        _status["last_error"] = None
        return result
    except Exception as exc:
        if db:
            db.rollback()
        _status["last_error"] = str(exc)
        _status["last_finished_at"] = _now()
        raise
    finally:
        if own_db:
            db.close()


async def _publish_review(review: Review) -> None:
    platform = (review.platform or "").upper()
    if platform == "WB":
        from app.services.publishing_service import publish_review
        res = publish_review(review)
        if asyncio.iscoroutine(res):
            await res
        return
    if platform == "OZON":
        from app.services.publishing_service import publish_review
        res = publish_review(review)
        if asyncio.iscoroutine(res):
            await res
        return
    raise RuntimeError(f"Публикация для площадки {platform} не подключена")


async def _publish_question(question: Question) -> None:
    platform = (question.platform or "").upper()
    from app.services.publishing_service import publish_question
    res = publish_question(question)
    if asyncio.iscoroutine(res):
        await res


async def autopublish_loop() -> None:
    while True:
        try:
            db = SessionLocal()
            try:
                # Цикл ничего не публикует, если выключен Render flag или правила.
                await autopublish_once(db)
            finally:
                db.close()
        except Exception as exc:
            _status["last_error"] = str(exc)
        await asyncio.sleep(max(300, int(getattr(settings, "autopublish_interval_seconds", 900) or 900)))


def get_autopublish_status() -> dict[str, Any]:
    _status["enabled"] = bool(settings.enable_marketplace_publishing)
    _status["mode"] = "real_publish" if settings.enable_marketplace_publishing else "dry_run"
    return dict(_status)
