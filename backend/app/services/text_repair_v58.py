from __future__ import annotations

from typing import Any
import json


def _looks_like_product_or_question_url(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    u = url.lower()
    return "ozon.ru/product/" in u or "wildberries.ru/catalog/" in u or "/questions/" in u


def _mojibake_score(value: str) -> int:
    cyr = sum(1 for ch in value if ("а" <= ch.lower() <= "я") or ch.lower() == "ё")
    emoji = sum(1 for ch in value if ord(ch) >= 0x1F000)
    bad = sum(value.count(x) for x in ["Р", "С", "рџ", "Ð", "Ñ", "В·", "в„", "�"])
    return cyr * 4 + emoji * 8 - bad * 5


def _decode_utf8_misread_as_cp1251(value: str) -> str | None:
    # Works for both:
    #   "РљР»РёРµРЅС‚" -> "Клиент"
    #   "РРЅРЅР°"       -> "Инна" where U+0098 is a lost cp1251 byte.
    buf = bytearray()
    for ch in value:
        o = ord(ch)
        if 0x80 <= o <= 0x9F:
            buf.append(o)
            continue
        try:
            buf.extend(ch.encode("cp1251"))
            continue
        except Exception:
            pass
        try:
            buf.extend(ch.encode("latin1"))
            continue
        except Exception:
            pass
        # ASCII is valid in all encodings; for emoji or symbols keep the original bytes,
        # but they usually do not appear inside the mojibake fragment.
        buf.extend(ch.encode("utf-8", errors="ignore"))
    try:
        return bytes(buf).decode("utf-8")
    except Exception:
        return None


def fix_text_v58(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value

    markers = ("Р", "С", "рџ", "Ð", "Ñ", "В·", "в„", "\x80", "\x81", "\x82", "\x83", "\x84", "\x85", "\x86", "\x87", "\x88", "\x89", "\x8a", "\x8b", "\x8c", "\x8d", "\x8e", "\x8f", "\x90", "\x91", "\x92", "\x93", "\x94", "\x95", "\x96", "\x97", "\x98", "\x99", "\x9a", "\x9b", "\x9c", "\x9d", "\x9e", "\x9f")
    if not any(m in value for m in markers):
        return value

    candidates = [value]
    d = _decode_utf8_misread_as_cp1251(value)
    if d:
        candidates.append(d)
    for enc in ("cp1251", "latin1"):
        try:
            candidates.append(value.encode(enc, errors="ignore").decode("utf-8", errors="ignore"))
        except Exception:
            pass

    best = max(candidates, key=_mojibake_score)
    return best or value


def repair_tree_v58(value: Any) -> Any:
    if isinstance(value, str):
        return fix_text_v58(value)
    if isinstance(value, list):
        return [repair_tree_v58(x) for x in value]
    if isinstance(value, dict):
        return {k: repair_tree_v58(v) for k, v in value.items()}
    return value


def _clean_media_list(media: Any) -> list[dict[str, Any]]:
    if not isinstance(media, list):
        return []
    out = []
    seen = set()
    for item in media:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        preview = item.get("preview_url")
        if _looks_like_product_or_question_url(url):
            continue
        if _looks_like_product_or_question_url(preview):
            item = dict(item)
            item["preview_url"] = None
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


def _is_technical_chat_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.count("-") >= 3 or value.startswith("1:")


def _enhance_item(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    platform = str(item.get("platform") or raw.get("platform") or "").upper()

    # Question link should be a normal link, not media and not hidden only inside raw.
    question_link = raw.get("question_link") or raw.get("questionUrl") or raw.get("question_url")
    if question_link:
        item["question_url"] = question_link

    # Chat title/subtitle: do not use UUID as the human subtitle.
    if "technical_chat_id" in item:
        if item.get("title") and _is_technical_chat_id(item.get("title")):
            item["title"] = item.get("client_name") or item.get("product_name") or f"Клиент {platform}".strip()
        if _is_technical_chat_id(item.get("display_subtitle")):
            item["display_subtitle"] = item.get("product_name") or item.get("order_number") or "товар/заказ не передан API"
        if not item.get("title"):
            item["title"] = item.get("client_name") or item.get("product_name") or f"Клиент {platform}".strip()

    # Product URL must be a product URL only.
    purl = item.get("product_url")
    if purl and not ("ozon.ru/product/" in str(purl).lower() or "wildberries.ru/catalog/" in str(purl).lower()):
        item["product_url"] = None

    # Media must be real media only, not product/question URLs.
    item["media"] = _clean_media_list(item.get("media"))

    return item


def repair_payload_v58(payload: Any) -> Any:
    payload = repair_tree_v58(payload)

    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            payload["items"] = [_enhance_item(x) if isinstance(x, dict) else x for x in payload["items"]]
        if isinstance(payload.get("chat"), dict):
            payload["chat"] = _enhance_item(payload["chat"])
        if isinstance(payload.get("media"), list):
            payload["media"] = _clean_media_list(payload["media"])

    return payload
