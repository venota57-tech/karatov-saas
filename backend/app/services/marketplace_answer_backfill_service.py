from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Review, Question

ANSWER_SOURCE_STATUSES = {
    "wb_answered",
    "wb_archive",
    "ozon_answered",
}

ANSWER_KEYS = {
    "answer",
    "answerText",
    "answer_text",
    "sellerAnswer",
    "seller_answer",
    "supplierAnswer",
    "supplier_answer",
    "response",
    "responseText",
    "response_text",
    "commentAnswer",
    "officialAnswer",
    "publishedAnswer",
    "textAnswer",
    "seller_comment",
    "comment_text",
    "commentText",
}

ANSWER_DATE_KEYS = {
    "answered_at",
    "answerDate",
    "answer_date",
    "answeredAt",
    "responseDate",
    "response_date",
    "answerCreatedAt",
    "answer_created_at",
    "answerUpdatedAt",
    "answer_updated_at",
}


def _walk(obj: Any, keys: set[str]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in (None, "", []):
                return value
        for value in obj.values():
            found = _walk(value, keys)
            if found not in (None, "", []):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _walk(value, keys)
            if found not in (None, "", []):
                return found
    return None


def _answer_text(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        for key in ("text", "answer", "answerText", "response", "comment", "seller_comment"):
            if value.get(key):
                return str(value.get(key)).strip() or None
        return None
    if isinstance(value, list):
        for item in reversed(value):
            text = _answer_text(item)
            if text:
                return text
        return None
    return str(value).strip() or None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, dict):
        value = _walk(value, ANSWER_DATE_KEYS)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _apply_row(row: Any) -> bool:
    raw = row.raw or {}
    answer = _answer_text(_walk(raw, ANSWER_KEYS)) or (row.final_answer.strip() if row.final_answer else None)
    answered_at = _parse_dt(_walk(raw, ANSWER_DATE_KEYS))

    marketplace_answered = (
        bool(answer)
        or bool(row.has_answer)
        or (row.source_status in ANSWER_SOURCE_STATUSES)
    )

    if not marketplace_answered:
        return False

    changed = False

    if answer and row.final_answer != answer:
        row.final_answer = answer
        changed = True

    if answer and not row.draft_answer:
        row.draft_answer = answer
        changed = True

    if row.has_answer is not True:
        row.has_answer = True
        changed = True

    if row.response_origin not in {"auto_app", "manual_app", "seller_cabinet"}:
        row.response_origin = "seller_cabinet"
        changed = True
    elif row.response_origin is None:
        row.response_origin = "seller_cabinet"
        changed = True

    if row.source_status in ANSWER_SOURCE_STATUSES and row.operational_status != "analytics_only":
        row.operational_status = "analytics_only"
        changed = True

    if row.status in {None, "", "new", "ready_to_review", "ready_to_publish"} and row.source_status in ANSWER_SOURCE_STATUSES:
        row.status = "answered_on_marketplace"
        changed = True

    if answered_at and not row.answered_at:
        row.answered_at = answered_at
        changed = True

    return changed


def backfill_marketplace_answers(db: Session, limit: int = 10000) -> dict[str, Any]:
    safe_limit = min(max(int(limit or 10000), 1), 50000)

    stats = {
        "ok": True,
        "limit": safe_limit,
        "reviews_scanned": 0,
        "questions_scanned": 0,
        "reviews_updated": 0,
        "questions_updated": 0,
        "note": "answered_at заполняется только если дата ответа есть в raw payload; если даты нет, SLA останется unknown вместо фейкового времени.",
    }

    review_rows = (
        db.query(Review)
        .filter(
            (Review.has_answer == True)  # noqa: E712
            | (Review.final_answer.isnot(None))
            | (Review.source_status.in_(list(ANSWER_SOURCE_STATUSES)))
        )
        .limit(safe_limit)
        .all()
    )

    for row in review_rows:
        stats["reviews_scanned"] += 1
        if _apply_row(row):
            stats["reviews_updated"] += 1

    question_rows = (
        db.query(Question)
        .filter(
            (Question.has_answer == True)  # noqa: E712
            | (Question.final_answer.isnot(None))
            | (Question.source_status.in_(["wb_answered", "ozon_answered"]))
        )
        .limit(safe_limit)
        .all()
    )

    for row in question_rows:
        stats["questions_scanned"] += 1
        if _apply_row(row):
            stats["questions_updated"] += 1

    db.commit()
    return stats
