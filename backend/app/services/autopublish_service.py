from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from ..config import settings
from ..database import SessionLocal
from sqlalchemy import or_
from ..models import Review, Question
from ..ai.answer_generator import AnswerGenerator
from ..services.automation_rules import get_rules, update_rules, apply_publication_rules, DEFAULT_RULES
from ..services.publishing_service import publish_review, publish_question


SUPPORTED_PLATFORMS = ["WB", "OZON", "YM"]


def _normalize_matrix(matrix: dict | None) -> dict[str, dict[str, bool]]:
    matrix = matrix if isinstance(matrix, dict) else {}
    result: dict[str, dict[str, bool]] = {}
    for platform in SUPPORTED_PLATFORMS:
        raw = matrix.get(platform) or matrix.get(platform.lower()) or {}
        result[platform] = {
            "reviews": bool(raw.get("reviews")),
            "questions": bool(raw.get("questions")),
        }
    return result


def _merged_rules(db) -> dict[str, Any]:
    row = get_rules(db)
    rules = dict(DEFAULT_RULES)
    rules.update(row.rules or {})
    rules["autopublish_matrix"] = _normalize_matrix(rules.get("autopublish_matrix"))
    return rules


def get_autopublish_rules(db) -> dict[str, Any]:
    rules = _merged_rules(db)
    rules["openai"] = {
        "api_key_found": bool(settings.openai_api_key),
        "model": settings.openai_model,
        "ai_generation_enabled": bool(rules.get("ai_generation_enabled", True)),
        "fallback_to_local_templates": bool(rules.get("ai_fallback_to_local_templates", True)),
    }
    rules["publishing"] = {
        "enable_marketplace_publishing": bool(settings.enable_marketplace_publishing),
        "mode": "real_publish" if settings.enable_marketplace_publishing else "dry_run",
    }
    return rules


def save_autopublish_rules(db, incoming: dict[str, Any]) -> dict[str, Any]:
    current = _merged_rules(db)
    payload = dict(current)
    payload.update(incoming or {})
    payload["autopublish_matrix"] = _normalize_matrix(payload.get("autopublish_matrix"))
    row = update_rules(db, payload)
    return get_autopublish_rules(db)


def _platform_enabled(rules: dict[str, Any], platform: str, content_type: str) -> bool:
    matrix = _normalize_matrix(rules.get("autopublish_matrix"))
    return bool(matrix.get((platform or "").upper(), {}).get(content_type))


def _is_publishable_source(obj, content_type: str) -> bool:
    platform = (obj.platform or "").upper()
    expected = {
        "WB": "wb_unanswered",
        "OZON": "ozon_unanswered",
        "YM": "ym_unanswered",
    }.get(platform)
    status = (obj.status or "").lower()
    operational = (obj.operational_status or "").lower()
    return bool(
        obj.source_status == expected
        and obj.has_answer is not True
        and (operational in {"", "needs_response"} or status in {"ready_to_publish", "ready_to_review"})
    )


def _answer_payload(obj, content_type: str) -> dict[str, Any]:
    payload = {
        "platform": obj.platform,
        "sku": obj.sku,
        "product_name": obj.product_name,
        "text": obj.text,
        "client_name": obj.client_name,
    }
    if content_type == "reviews":
        payload.update({"rating": obj.rating, "pros": obj.pros, "cons": obj.cons})
    return payload


def _save_generation(obj, result: dict[str, Any], content_type: str) -> None:
    obj.ai_category = result.get("category")
    if hasattr(obj, "ai_sentiment"):
        obj.ai_sentiment = result.get("sentiment")
    obj.ai_risk_level = result.get("risk_level")
    obj.ai_can_autopublish = bool(result.get("can_autopublish"))
    obj.ai_reason = result.get("reason")
    obj.ai_tags = result.get("tags") or obj.ai_tags
    obj.draft_answer = result.get("answer_text") or None
    obj.final_answer = result.get("answer_text") or None
    if not obj.final_answer:
        obj.status = "answer_rejected_quality_gate"
        obj.publish_blocked_reason = result.get("reason") or "Ответ не прошел quality gate 10/10"
    else:
        obj.status = "ready_to_publish" if _is_publishable_source(obj, content_type) else "local_draft"
        obj.publish_blocked_reason = None
    obj.updated_at = datetime.utcnow()


def _rows_for(db, model, rules: dict[str, Any], content_type: str, limit: int):
    enabled_platforms = [p for p in SUPPORTED_PLATFORMS if _platform_enabled(rules, p, content_type)]
    if not enabled_platforms:
        return []
    source_statuses = [f"{p.lower()}_unanswered" for p in enabled_platforms]
    return (
        db.query(model)
        .filter(model.platform.in_(enabled_platforms))
        .filter(model.source_status.in_(source_statuses))
        .filter(or_(model.has_answer == False, model.has_answer.is_(None)))  # noqa: E712
        .filter(or_(
            model.operational_status == "needs_response",
            model.operational_status.is_(None),
            model.status.in_(["ready_to_publish", "ready_to_review"])
        ))
        .order_by(model.created_at_marketplace.asc().nullslast(), model.id.asc())
        .limit(limit)
        .all()
    )


async def _process_one(db, obj, content_type: str, rules: dict[str, Any], generator: AnswerGenerator) -> dict[str, Any]:
    if not _platform_enabled(rules, obj.platform, content_type):
        return {"status": "skipped", "reason": "autopublish disabled for platform/content_type"}
    if not _is_publishable_source(obj, content_type):
        obj.publish_blocked_reason = "Не находится в актуальной очереди площадки “без ответа”."
        obj.updated_at = datetime.utcnow()
        db.commit()
        return {"status": "skipped", "reason": obj.publish_blocked_reason}

    # Нормализуем старые строки перед публикацией, чтобы publish_service не отклонил их.
    obj.operational_status = "needs_response"
    obj.has_answer = False

    if not (obj.final_answer or "").strip():
        payload = _answer_payload(obj, content_type)
        if content_type == "reviews":
            result = generator.generate_for_review_until_pass(payload)
            result["platform"] = obj.platform
            result = apply_publication_rules(result, "review", obj.rating, db)
        else:
            result = generator.generate_for_question_until_pass(payload)
            result["platform"] = obj.platform
            result = apply_publication_rules(result, "question", None, db)
        _save_generation(obj, result, content_type)
        db.commit()

    if not (obj.final_answer or "").strip():
        return {"status": "skipped", "reason": obj.publish_blocked_reason or "no final answer"}

    # The matrix is the user's final permission. Risk/category rules still may block via ai_can_autopublish.
    if rules.get("autopublish_require_ai_can_autopublish", False) and not obj.ai_can_autopublish:
        obj.publish_blocked_reason = obj.ai_reason or "ai_can_autopublish=false"
        obj.updated_at = datetime.utcnow()
        db.commit()
        return {"status": "skipped", "reason": obj.publish_blocked_reason}

    if content_type == "reviews":
        result = await publish_review(db, obj.id, response_origin='auto_app')
    else:
        result = await publish_question(db, obj.id, response_origin='auto_app')

    # In dry-run mode publish_* intentionally does not mark has_answer. For operational clarity mark it as ready, not archived.
    if result.get("status") == "dry_run":
        obj.status = "ready_to_publish"
        obj.operational_status = "needs_response"
        obj.publish_blocked_reason = "Dry-run: ENABLE_MARKETPLACE_PUBLISHING=false, ответ подготовлен, но не отправлен."
        db.commit()
        return {"status": "dry_run", "reason": obj.publish_blocked_reason}

    return {"status": "published", "message": result.get("message")}


async def autopublish_once() -> dict[str, Any]:
    db = SessionLocal()
    stats = {
        "ok": True,
        "checked": 0,
        "generated_or_used_existing": 0,
        "published": 0,
        "dry_run": 0,
        "skipped": 0,
        "errors": [],
    }
    try:
        rules = _merged_rules(db)
        if not rules.get("real_autopublish_enabled", False):
            return {**stats, "reason": "real_autopublish_enabled=false"}

        limit = max(1, min(100, int(rules.get("autopublish_max_per_run", 10))))
        pause_between = max(1, int(rules.get("autopublish_pause_between_items_seconds", 8)))
        generator = AnswerGenerator(rules)

        tasks = [
            ("reviews", Review, _rows_for(db, Review, rules, "reviews", limit)),
            ("questions", Question, _rows_for(db, Question, rules, "questions", limit)),
        ]

        for content_type, _model, rows in tasks:
            for obj in rows:
                if stats["checked"] >= limit:
                    break
                stats["checked"] += 1
                try:
                    before_answer = bool((obj.final_answer or "").strip())
                    result = await _process_one(db, obj, content_type, rules, generator)
                    if not before_answer and (obj.final_answer or "").strip():
                        stats["generated_or_used_existing"] += 1
                    if result["status"] == "published":
                        stats["published"] += 1
                    elif result["status"] == "dry_run":
                        stats["dry_run"] += 1
                    else:
                        stats["skipped"] += 1
                    await asyncio.sleep(pause_between)
                except Exception as exc:
                    db.rollback()
                    obj.publish_blocked_reason = str(exc)[:1000]
                    obj.updated_at = datetime.utcnow()
                    db.commit()
                    stats["errors"].append({"id": obj.id, "platform": obj.platform, "type": content_type, "error": str(exc)})
                    stats["skipped"] += 1
        return stats
    finally:
        db.close()


async def autopublish_loop():
    await asyncio.sleep(20)
    while True:
        interval = 900
        try:
            result = await autopublish_once()
            print(f"[autopublish] result: {result}")
            db = SessionLocal()
            try:
                rules = _merged_rules(db)
                interval = max(60, int(rules.get("autopublish_interval_seconds", 900)))
            finally:
                db.close()
        except Exception as e:
            print(f"[autopublish] loop error: {e}")
        await asyncio.sleep(interval)
