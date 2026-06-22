from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Question, Review


WB_BASE = "https://feedbacks-api.wildberries.ru"
OZON_BASE = "https://api-seller.ozon.ru"


def _pick_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ["answer", "answerText", "text", "comment", "message", "content", "final_answer", "supplierAnswer", "sellerAnswer"]:
            if key in value:
                found = _pick_text(value.get(key))
                if found:
                    return found
        for key, inner in value.items():
            if str(key).lower() in {"answers", "comments", "answer", "seller_comment", "sellercomment"}:
                found = _pick_text(inner)
                if found:
                    return found
        for inner in value.values():
            found = _pick_text(inner)
            if found:
                return found
    if isinstance(value, list):
        for item in reversed(value):
            found = _pick_text(item)
            if found:
                return found
    return None


def _pick_id(row: dict[str, Any]) -> str | None:
    for key in ["id", "feedbackId", "feedback_id", "review_id", "reviewId", "question_id", "questionId", "uuid"]:
        value = row.get(key)
        if value:
            return str(value)
    return None


def _apply_answer(obj, answer: str, origin: str = "seller_cabinet") -> bool:
    if not answer:
        return False
    if getattr(obj, "final_answer", None) == answer and getattr(obj, "has_answer", False):
        return False
    obj.final_answer = answer
    obj.has_answer = True
    obj.response_origin = origin
    obj.answered_at = getattr(obj, "answered_at", None) or datetime.utcnow()
    if hasattr(obj, "operational_status"):
        obj.operational_status = "answered"
    if hasattr(obj, "status"):
        obj.status = "answered"
    return True


def _find_review(db: Session, platform_aliases: list[str], external_id: str):
    return db.query(Review).filter(Review.platform.in_(platform_aliases)).filter(Review.external_id == external_id).first()


def _find_question(db: Session, platform_aliases: list[str], external_id: str):
    return db.query(Question).filter(Question.platform.in_(platform_aliases)).filter(Question.external_id == external_id).first()


def enrich_wb_published_answers(db: Session, limit: int = 1000) -> dict[str, Any]:
    token = getattr(settings, "wb_api_token", None) or getattr(settings, "wb_api_key", None)
    if not token:
        return {"ok": False, "status": "not_connected", "platform": "WB", "error": "WB token is not configured"}
    headers = {"Authorization": token}
    aliases = ["WB", "WILDBERRIES", "WILDBERRY", "ВБ"]
    updated_reviews = updated_questions = scanned_reviews = scanned_questions = 0
    errors: list[str] = []
    with httpx.Client(base_url=WB_BASE, headers=headers, timeout=20.0) as client:
        for endpoint, model_type in [("/api/v1/feedbacks", "review"), ("/api/v1/questions", "question")]:
            skip = 0
            take = 100
            while skip < max(limit, 1):
                try:
                    response = client.get(endpoint, params={"isAnswered": "true", "take": take, "skip": skip, "order": "dateDesc"})
                    if response.status_code in {401, 403}:
                        return {"ok": False, "status": "permission_error", "platform": "WB", "endpoint": endpoint, "error": response.text[:500]}
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    errors.append(f"{endpoint}: {exc}")
                    break
                data = payload.get("data", payload)
                rows = data.get("feedbacks") or data.get("questions") or data.get("items") or data.get("data") or []
                if isinstance(rows, dict):
                    rows = list(rows.values())
                if not isinstance(rows, list) or not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    external_id = _pick_id(row)
                    answer = _pick_text(row.get("answer")) or _pick_text(row.get("answers")) or _pick_text(row)
                    if not external_id or not answer:
                        continue
                    if model_type == "review":
                        scanned_reviews += 1
                        obj = _find_review(db, aliases, external_id)
                        if obj and _apply_answer(obj, answer):
                            updated_reviews += 1
                    else:
                        scanned_questions += 1
                        obj = _find_question(db, aliases, external_id)
                        if obj and _apply_answer(obj, answer):
                            updated_questions += 1
                db.commit()
                if len(rows) < take:
                    break
                skip += take
    return {"ok": True, "status": "success" if not errors else "partial", "platform": "WB", "scanned_reviews": scanned_reviews, "updated_reviews": updated_reviews, "scanned_questions": scanned_questions, "updated_questions": updated_questions, "errors": errors[:10]}


def _ozon_headers() -> dict[str, str]:
    return {"Client-Id": str(settings.ozon_client_id or ""), "Api-Key": str(settings.ozon_api_key or ""), "Content-Type": "application/json"}


def _ozon_post(client: httpx.Client, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(endpoint, json=payload)
    if response.status_code in {401, 403}:
        raise PermissionError(response.text[:500])
    response.raise_for_status()
    return response.json()


def enrich_ozon_published_answers(db: Session, limit: int = 1000) -> dict[str, Any]:
    if not settings.ozon_client_id or not settings.ozon_api_key:
        return {"ok": False, "status": "not_connected", "platform": "OZON", "error": "Ozon credentials are not configured"}
    aliases = ["OZON", "OZON.RU", "ОЗОН"]
    updated_reviews = updated_questions = scanned_reviews = scanned_questions = 0
    errors: list[str] = []
    with httpx.Client(base_url=OZON_BASE, headers=_ozon_headers(), timeout=25.0) as client:
        try:
            last_id = None
            while scanned_reviews < max(limit, 1):
                payload: dict[str, Any] = {"limit": min(100, max(limit - scanned_reviews, 1)), "status": "PROCESSED"}
                if last_id:
                    payload["last_id"] = last_id
                data = _ozon_post(client, "/v1/review/list", payload)
                rows = data.get("reviews") or data.get("result", {}).get("reviews") or []
                if not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    review_id = str(row.get("id") or row.get("review_id") or row.get("reviewId") or "")
                    if not review_id:
                        continue
                    scanned_reviews += 1
                    answer = _pick_text(row.get("comments")) or _pick_text(row.get("answer")) or _pick_text(row.get("seller_comment"))
                    if not answer:
                        try:
                            answer = _pick_text(_ozon_post(client, "/v1/review/comment/list", {"review_id": review_id}))
                        except Exception as exc:
                            errors.append(f"review_comment:{review_id}:{exc}")
                    if answer:
                        obj = _find_review(db, aliases, review_id)
                        if obj and _apply_answer(obj, answer):
                            updated_reviews += 1
                db.commit()
                last_id = data.get("last_id") or data.get("result", {}).get("last_id")
                if not last_id or len(rows) < payload["limit"]:
                    break
        except PermissionError as exc:
            return {"ok": False, "status": "permission_error", "platform": "OZON", "error": str(exc)}
        except Exception as exc:
            errors.append(f"review_list:{exc}")
        try:
            last_id = None
            while scanned_questions < max(limit, 1):
                payload = {"limit": min(100, max(limit - scanned_questions, 1))}
                if last_id:
                    payload["last_id"] = last_id
                data = _ozon_post(client, "/v1/question/list", payload)
                rows = data.get("questions") or data.get("result", {}).get("questions") or []
                if not rows:
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    question_id = str(row.get("id") or row.get("question_id") or row.get("questionId") or "")
                    if not question_id:
                        continue
                    scanned_questions += 1
                    answer = _pick_text(row.get("answers")) or _pick_text(row.get("answer"))
                    if not answer:
                        try:
                            answer = _pick_text(_ozon_post(client, "/v1/question/answer/list", {"question_id": question_id}))
                        except Exception as exc:
                            errors.append(f"question_answer:{question_id}:{exc}")
                    if answer:
                        obj = _find_question(db, aliases, question_id)
                        if obj and _apply_answer(obj, answer):
                            updated_questions += 1
                db.commit()
                last_id = data.get("last_id") or data.get("result", {}).get("last_id")
                if not last_id or len(rows) < payload["limit"]:
                    break
        except PermissionError as exc:
            return {"ok": False, "status": "permission_error", "platform": "OZON", "error": str(exc)}
        except Exception as exc:
            errors.append(f"question_list:{exc}")
    return {"ok": True, "status": "success" if not errors else "partial", "platform": "OZON", "scanned_reviews": scanned_reviews, "updated_reviews": updated_reviews, "scanned_questions": scanned_questions, "updated_questions": updated_questions, "errors": errors[:20]}


def enrich_all_published_answers(db: Session, limit: int = 1000) -> dict[str, Any]:
    return {"ok": True, "wb": enrich_wb_published_answers(db, limit=limit), "ozon": enrich_ozon_published_answers(db, limit=limit)}
