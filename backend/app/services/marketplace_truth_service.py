from __future__ import annotations

from datetime import datetime
from typing import Any

ANSWER_KEYS = {
    "answer", "answerText", "answer_text",
    "sellerAnswer", "seller_answer",
    "supplierAnswer", "supplier_answer",
    "response", "responseText", "response_text",
    "commentAnswer", "officialAnswer", "publishedAnswer",
    "textAnswer", "answer_text_html",
    "answer_text_plain", "answerTextPlain"
}

ANSWER_DATE_KEYS = {
    "answered_at", "answerDate", "answer_date", "answeredAt",
    "responseDate", "response_date",
    "answerCreatedAt", "answer_created_at",
    "answerUpdatedAt", "answer_updated_at",
    "updatedAt", "updated_at"
}

def walk_value(obj: Any, keys: set[str]) -> Any:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and v not in (None, ""):
                return v
        for v in obj.values():
            found = walk_value(v, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = walk_value(v, keys)
            if found not in (None, ""):
                return found
    return None

def parse_dt(value: Any):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None

def apply_marketplace_answer(data: dict, raw: Any, *, force_answered: bool = False) -> dict:
    answer = walk_value(raw, ANSWER_KEYS)
    answered_at = parse_dt(walk_value(raw, ANSWER_DATE_KEYS))

    if answer:
        data["final_answer"] = str(answer)
        data["draft_answer"] = str(answer)
        data["has_answer"] = True
        data["response_origin"] = "seller_cabinet"
        data["status"] = "answered_on_marketplace"
        if answered_at:
            data["answered_at"] = answered_at
    elif force_answered:
        data["has_answer"] = True
        data["response_origin"] = data.get("response_origin") or "seller_cabinet"
        if answered_at:
            data["answered_at"] = answered_at

    return data
