from __future__ import annotations

import hashlib
import json
from typing import Any
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


def dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"raw": str(value)}, ensure_ascii=False)


def loads(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def sha(value: Any) -> str:
    return hashlib.sha1(dumps(value).encode("utf-8")).hexdigest()[:32]



def _mojibake_score(value: str) -> int:
    cyr = sum(1 for ch in value if ("а" <= ch.lower() <= "я") or ch.lower() == "ё")
    emoji = sum(1 for ch in value if ord(ch) > 0x1F000)
    bad = sum(value.count(x) for x in ["Р", "С", "рџ", "Ð", "Ñ", "В·", "в„", "�"])
    return cyr * 3 + emoji * 6 - bad * 4


def _decode_cp1251_mojibake(value: str) -> str | None:
    buf = bytearray()
    for ch in value:
        o = ord(ch)
        if 0x80 <= o <= 0x9F:
            buf.append(o)
            continue
        try:
            buf.extend(ch.encode("cp1251"))
        except Exception:
            try:
                buf.extend(ch.encode("latin1"))
            except Exception:
                buf.extend(ch.encode("utf-8", errors="ignore"))
    try:
        return bytes(buf).decode("utf-8")
    except Exception:
        return None


def fix_text(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    markers = ["Р", "С", "рџ", "Ð", "Ñ", "В·", "в„", "\x98", "\x9d", "\x8f", "\x81"]
    if not any(m in value for m in markers):
        return value
    candidates = []
    c = _decode_cp1251_mojibake(value)
    if c:
        candidates.append(c)
    for enc in ("cp1251", "latin1"):
        try:
            candidates.append(value.encode(enc, errors="ignore").decode("utf-8", errors="ignore"))
        except Exception:
            pass
    best = value
    best_score = _mojibake_score(value)
    for cand in candidates:
        if not cand:
            continue
        score = _mojibake_score(cand)
        if score > best_score:
            best, best_score = cand, score
    return best
def fix_tree(value: Any) -> Any:
    if isinstance(value, str):
        return fix_text(value)
    if isinstance(value, list):
        return [fix_tree(x) for x in value]
    if isinstance(value, dict):
        return {k: fix_tree(v) for k, v in value.items()}
    return value


def walk(obj: Any):
    obj = fix_tree(loads(obj) or obj)
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def get_any(obj: Any, *keys: str, default: Any = None) -> Any:
    obj = loads(obj) or obj
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj.get(key) not in (None, ""):
                return obj.get(key)
        for value in obj.values():
            found = get_any(value, *keys, default=None)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = get_any(value, *keys, default=None)
            if found not in (None, ""):
                return found
    return default


def parse_dt(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def maybe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v if v.startswith("http://") or v.startswith("https://") or v.startswith("data:") else None


def media_kind(url: str | None = None, mime: str | None = None, typ: str | None = None) -> str:
    t = f"{url or ''} {mime or ''} {typ or ''}".lower()
    if any(x in t for x in ["image", "photo", ".jpg", ".jpeg", ".png", ".webp", ".gif"]):
        return "image"
    if any(x in t for x in ["video", ".mp4", ".mov", ".webm"]):
        return "video"
    if any(x in t for x in ["audio", ".mp3", ".wav", ".ogg"]):
        return "audio"
    return "file"



def is_product_or_question_url(url: str | None) -> bool:
    if not url:
        return False
    u = str(url).lower()
    return "ozon.ru/product/" in u or "wildberries.ru/catalog/" in u or "/questions/" in u


def extract_media_v56(raw: Any) -> list[dict[str, Any]]:
    raw = fix_tree(loads(raw) or raw)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    media_container_keys = {"photo", "photos", "photolinks", "photoLinks", "images", "image", "pictures", "picture", "video", "videos", "attachments", "attachment", "files", "file", "media", "documents", "document"}
    media_container_keys_lower = {str(k).lower() for k in media_container_keys}
    url_keys = ["url", "link", "src", "href", "preview", "previewUrl", "preview_url", "thumbnail", "thumbnailUrl", "fileUrl", "file_url", "imageUrl", "image_url", "videoUrl", "video_url", "fullSize", "miniSize", "big", "small", "origin", "original"]
    id_keys = ["downloadID", "downloadId", "download_id", "mediaId", "media_id", "fileId", "file_id", "photoId", "photo_id", "videoId", "video_id", "id", "uuid"]

    def add(kind: str | None, url: str | None, preview: str | None, ext_id: Any, filename: Any, mime: Any, payload: Any) -> None:
        if is_product_or_question_url(url):
            return
        if is_product_or_question_url(preview):
            preview = None
        if not (url or ext_id or filename):
            return
        kind = kind or media_kind(url, str(mime or ""), str(get_any(payload, "type", "mediaType", default="") or ""))
        marker = f"{kind}:{url or ''}:{ext_id or ''}:{filename or ''}"
        if marker in seen:
            return
        seen.add(marker)
        out.append({
            "media_type": kind,
            "url": url,
            "preview_url": preview or (url if kind == "image" else None),
            "external_media_id": str(ext_id) if ext_id not in (None, "") else None,
            "filename": filename or get_any(payload, "filename", "fileName", "name", "title", default=None),
            "mime_type": mime or get_any(payload, "mimeType", "contentType", "content_type", default=None),
            "source": "marketplace",
            "visibility": "marketplace_visible" if url else "marketplace_reference",
            "send_status": "received" if url else "reference_only",
            "raw_payload": payload,
        })

    for node in walk(raw):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            low = str(key).lower()
            if low not in media_container_keys_lower:
                continue
            if isinstance(value, str):
                u = maybe_url(value)
                if u:
                    add(media_kind(url=u, typ=low), u, None, get_any(node, *id_keys, default=None), get_any(node, "filename", "fileName", "name", "title", default=None), get_any(node, "mimeType", "contentType", default=None), {key: value})
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        u = maybe_url(item)
                        if u:
                            add(media_kind(url=u, typ=low), u, None, None, None, None, {key: item})
                    elif isinstance(item, dict):
                        urls = [maybe_url(item.get(k)) for k in url_keys if k in item]
                        u = next((x for x in urls if x and not is_product_or_question_url(x)), None)
                        preview = maybe_url(item.get("previewUrl") or item.get("preview_url") or item.get("thumbnail") or item.get("miniSize"))
                        add(media_kind(url=u, mime=get_any(item, "mimeType", "contentType", default=None), typ=get_any(item, "type", "mediaType", default=low)), u, preview, get_any(item, *id_keys, default=None), get_any(item, "filename", "fileName", "name", "title", default=None), get_any(item, "mimeType", "contentType", default=None), item)
            elif isinstance(value, dict):
                urls = [maybe_url(value.get(k)) for k in url_keys if k in value]
                u = next((x for x in urls if x and not is_product_or_question_url(x)), None)
                preview = maybe_url(value.get("previewUrl") or value.get("preview_url") or value.get("thumbnail") or value.get("miniSize"))
                add(media_kind(url=u, mime=get_any(value, "mimeType", "contentType", default=None), typ=get_any(value, "type", "mediaType", default=low)), u, preview, get_any(value, *id_keys, default=None), get_any(value, "filename", "fileName", "name", "title", default=None), get_any(value, "mimeType", "contentType", default=None), value)

        if any(k in node for k in id_keys) and any(str(k).lower() in ["type", "mediatype", "contenttype", "mimetype"] for k in node.keys()):
            urls = [maybe_url(node.get(k)) for k in url_keys if k in node]
            u = next((x for x in urls if x and not is_product_or_question_url(x)), None)
            add(media_kind(url=u, mime=get_any(node, "mimeType", "contentType", default=None), typ=get_any(node, "type", "mediaType", default=None)), u, None, get_any(node, *id_keys, default=None), get_any(node, "filename", "fileName", "name", "title", default=None), get_any(node, "mimeType", "contentType", default=None), node)
    return out[:100]

def message_text_v56(raw: Any) -> str:
    raw = fix_tree(loads(raw) or raw)
    if raw is None:
        return ""
    if isinstance(raw, str):
        t = raw.strip()
        if t in {"-", "—", "None", "null"} or "текст не распознан" in t.lower():
            return ""
        return fix_text(t)
    if isinstance(raw, list):
        parts = [message_text_v56(x) for x in raw]
        return "\n".join([p for p in parts if p]).strip()
    if not isinstance(raw, dict):
        return ""
    main = fix_text(str(raw.get("text") or "").strip()) if raw.get("text") is not None else ""
    pros = fix_text(str(raw.get("pros") or "").strip()) if raw.get("pros") is not None else ""
    cons = fix_text(str(raw.get("cons") or "").strip()) if raw.get("cons") is not None else ""
    if main or pros or cons:
        parts = []
        if main:
            parts.append(main)
        if pros:
            parts.append(f"Достоинства: {pros}")
        if cons:
            parts.append(f"Недостатки: {cons}")
        return "\n".join(parts)
    for key in ["message_text", "messageText", "body", "content", "value", "comment", "caption", "description", "reviewText", "question", "answer", "plain_text", "plainText", "html"]:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return fix_text(value.strip())
        if isinstance(value, (dict, list)):
            nested = message_text_v56(value)
            if nested:
                return nested
    for key in ["message", "payload", "event", "item", "lastMessage", "last_message", "data", "blocks", "items", "elements"]:
        value = raw.get(key)
        if isinstance(value, (dict, list, str)):
            nested = message_text_v56(value)
            if nested:
                return nested
    return "[медиа]" if extract_media_v56(raw) else ""
def is_bad_text(value: Any) -> bool:
    if value is None:
        return True
    t = str(value).strip().lower()
    return not t or t in {"-", "—", "none", "null"} or "текст не распознан" in t or "обнови customer ops" in t



def product_from_raw(platform: str, row: dict[str, Any], raw: Any) -> dict[str, Any]:
    raw = fix_tree(loads(raw) or raw or {})
    product = get_any(raw, "product", "productDetails", "goodCard", "card", default={}) or {}
    products = get_any(raw, "products", "items", default=None)
    if isinstance(products, list) and products and isinstance(products[0], dict):
        product = products[0]
    if not isinstance(product, dict):
        product = {}
    sku = str(row.get("sku") or get_any(product, "nmID", "nmId", "sku", "offer_id", "offerId", "product_id", "productId", "id", default="") or get_any(raw, "nmID", "nmId", "sku", "offer_id", "offerId", "product_id", "productId", default="") or "")
    name = fix_text(row.get("product_name") or get_any(product, "name", "productName", "product_name", "subject", "title", default="") or get_any(raw, "productName", "product_name", "subject", "title", default="") or "")
    url = row.get("product_url")
    if not url and isinstance(product, dict):
        url = product.get("product_url") or product.get("productUrl")
    if not url and isinstance(raw, dict):
        url = raw.get("product_url") or raw.get("productUrl")
    if url and "/questions/" in str(url).lower():
        url = None
    platform = (platform or row.get("platform") or "").upper()
    if not url and platform == "WB" and sku.isdigit():
        url = f"https://www.wildberries.ru/catalog/{sku}/detail.aspx"
    if url and not ("ozon.ru/product/" in str(url).lower() or "wildberries.ru/catalog/" in str(url).lower()):
        url = None
    return {"sku": sku, "product_name": name, "product_url": url}
def order_from_raw(row: dict[str, Any], raw: Any) -> dict[str, Any]:
    raw = fix_tree(loads(raw) or raw or {})
    number = row.get("order_number") or row.get("posting_number") or get_any(raw, "posting_number", "postingNumber", "order_number", "orderNumber", "order_id", "orderId", "rid", "srid", "shipment_id", "shipmentId", default=None)
    url = get_any(raw, "order_url", "orderUrl", "posting_url", "postingUrl", default=None)
    kind = "posting_number" if get_any(raw, "posting_number", "postingNumber", default=None) else ("rid/srid" if get_any(raw, "rid", "srid", default=None) else "order_number")
    return {"order_number": str(number) if number else None, "order_url": url, "order_kind": kind}


def client_from_raw(row: dict[str, Any], raw: Any) -> str:
    raw = fix_tree(loads(raw) or raw or {})
    value = row.get("buyer_name") or row.get("client_name") or row.get("author_name") or get_any(raw, "buyer_name", "buyerName", "clientName", "customer_name", "customerName", "userName", "author", "senderName", "name", default=None)
    return fix_text(str(value)) if value else ""


def direction_from_raw(row: dict[str, Any], raw: Any) -> str:
    raw_text = dumps(raw).lower()
    direct = str(row.get("direction") or get_any(raw, "direction", "sender", "senderType", "authorType", "from", "side", default="")).lower()
    if any(x in direct for x in ["seller", "merchant", "operator", "продав", "оператор"]):
        return "seller"
    if any(x in direct for x in ["buyer", "customer", "client", "покуп", "клиент"]):
        return "customer"
    return "seller" if any(x in raw_text for x in ["seller", "merchant", "operator", "продав", "оператор"]) else "customer"


class HumanCommsV56:
    def __init__(self, db: Session):
        self.db = db

    def tables(self) -> set[str]:
        try:
            return set(inspect(self.db.bind).get_table_names())
        except Exception:
            self.db.rollback()
            return set()

    def cols(self, table_name: str) -> set[str]:
        try:
            return {c["name"] for c in inspect(self.db.bind).get_columns(table_name)}
        except Exception:
            self.db.rollback()
            return set()

    def ensure_media_schema(self) -> None:
        dialect = self.db.bind.dialect.name
        id_type = "SERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
        try:
            self.db.execute(text(f"""CREATE TABLE IF NOT EXISTS communication_media (
                id {id_type}, entity_type VARCHAR(64), entity_id VARCHAR(256), platform VARCHAR(32),
                external_media_id VARCHAR(256), media_type VARCHAR(32), url TEXT, preview_url TEXT,
                filename TEXT, mime_type VARCHAR(128), size_bytes INTEGER, source VARCHAR(64),
                visibility VARCHAR(64), send_status VARCHAR(64), content_base64 TEXT, raw_payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""))
            self.db.commit()
        except Exception:
            self.db.rollback()


    def cleanup_false_media(self) -> int:
        self.ensure_media_schema()
        sql_pg = "DELETE FROM communication_media WHERE url ILIKE '%ozon.ru/product/%' OR url ILIKE '%wildberries.ru/catalog/%' OR url ILIKE '%/questions/%' OR preview_url ILIKE '%ozon.ru/product/%' OR preview_url ILIKE '%wildberries.ru/catalog/%' OR preview_url ILIKE '%/questions/%'"
        sql_sqlite = "DELETE FROM communication_media WHERE lower(coalesce(url,'')) LIKE '%ozon.ru/product/%' OR lower(coalesce(url,'')) LIKE '%wildberries.ru/catalog/%' OR lower(coalesce(url,'')) LIKE '%/questions/%' OR lower(coalesce(preview_url,'')) LIKE '%ozon.ru/product/%' OR lower(coalesce(preview_url,'')) LIKE '%wildberries.ru/catalog/%' OR lower(coalesce(preview_url,'')) LIKE '%/questions/%'"
        for sql in (sql_pg, sql_sqlite):
            try:
                res = self.db.execute(text(sql))
                self.db.commit()
                return int(getattr(res, "rowcount", 0) or 0)
            except Exception:
                self.db.rollback()
        return 0

    def upsert_media(self, entity_type: str, entity_id: str, platform: str, raw: Any) -> int:
        self.ensure_media_schema()
        count = 0
        for item in extract_media_v56(raw):
            ext = str(item.get("external_media_id") or item.get("url") or item.get("filename") or sha(item))
            params = {"entity_type": entity_type, "entity_id": str(entity_id), "platform": (platform or "").upper(), "external_media_id": ext, "media_type": item.get("media_type") or "file", "url": item.get("url"), "preview_url": item.get("preview_url"), "filename": item.get("filename"), "mime_type": item.get("mime_type"), "size_bytes": item.get("size_bytes"), "source": item.get("source") or "marketplace", "visibility": item.get("visibility") or "marketplace_reference", "send_status": item.get("send_status") or "received", "content_base64": item.get("content_base64"), "raw_payload": dumps(item.get("raw_payload") or item)}
            try:
                exists = self.db.execute(text("SELECT id FROM communication_media WHERE entity_type=:entity_type AND entity_id=:entity_id AND platform=:platform AND external_media_id=:external_media_id"), params).first()
                if exists:
                    self.db.execute(text("UPDATE communication_media SET media_type=:media_type, url=COALESCE(:url,url), preview_url=COALESCE(:preview_url,preview_url), filename=COALESCE(:filename,filename), mime_type=COALESCE(:mime_type,mime_type), source=:source, visibility=:visibility, send_status=:send_status, raw_payload=:raw_payload, updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {**params, "id": exists[0]})
                else:
                    self.db.execute(text("INSERT INTO communication_media(entity_type,entity_id,platform,external_media_id,media_type,url,preview_url,filename,mime_type,size_bytes,source,visibility,send_status,content_base64,raw_payload,created_at,updated_at) VALUES (:entity_type,:entity_id,:platform,:external_media_id,:media_type,:url,:preview_url,:filename,:mime_type,:size_bytes,:source,:visibility,:send_status,:content_base64,:raw_payload,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), params)
                count += 1
            except Exception:
                self.db.rollback()
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
        return count


    def media_for(self, entity_type: str, entity_id: str, platform: str, raw: Any | None = None) -> list[dict[str, Any]]:
        self.ensure_media_schema()
        if raw is not None:
            self.upsert_media(entity_type, str(entity_id), platform, raw)
        rows = []
        try:
            rows = self.db.execute(text("SELECT media_type,url,preview_url,filename,mime_type,source,visibility,send_status,external_media_id,raw_payload FROM communication_media WHERE entity_type=:et AND entity_id=:eid AND platform=:p ORDER BY id DESC LIMIT 100"), {"et": entity_type, "eid": str(entity_id), "p": (platform or "").upper()}).mappings().all()
        except Exception:
            self.db.rollback()
        out = []
        for r in rows:
            d = dict(r)
            if is_product_or_question_url(d.get("url")):
                continue
            if is_product_or_question_url(d.get("preview_url")):
                d["preview_url"] = None
            d["raw_payload"] = loads(d.get("raw_payload"))
            out.append(fix_tree(d))
        return out if out else (extract_media_v56(raw) if raw is not None else [])
    def normalize_chat(self, row: dict[str, Any]) -> dict[str, Any]:
        raw = fix_tree(loads(row.get("raw")) or {})
        platform = str(row.get("platform") or get_any(raw, "platform", default="")).upper()
        product = product_from_raw(platform, row, raw)
        order = order_from_raw(row, raw)
        client = client_from_raw(row, raw)
        external_id = str(row.get("external_chat_id") or get_any(raw, "chat_id", "chatId", "chatID", "id", default="") or row.get("id"))
        title = client or product.get("product_name") or f"Клиент {platform or ''}".strip() or "Клиент"
        last_text = ""
        try:
            msg = self.db.execute(text("SELECT text, raw, media, sent_at, direction FROM buyer_chat_messages WHERE platform=:p AND external_chat_id=:cid ORDER BY COALESCE(sent_at,created_at) DESC,id DESC LIMIT 1"), {"p": platform, "cid": external_id}).mappings().first()
            if msg:
                msg_raw = loads(msg.get("raw")) or {}
                last_text = message_text_v56(msg_raw) if is_bad_text(msg.get("text")) else fix_text(msg.get("text"))
        except Exception:
            self.db.rollback()
        media = self.media_for("chat", external_id, platform, raw)
        return fix_tree({"id": row.get("id"), "platform": platform, "title": title, "client_name": client, "technical_chat_id": external_id, "technical_chat_id_hidden": True, "display_subtitle": product.get("product_name") or order.get("order_number") or external_id, "product_name": product.get("product_name"), "sku": product.get("sku"), "product_url": product.get("product_url"), "order_number": order.get("order_number"), "order_url": order.get("order_url"), "order_kind": order.get("order_kind"), "status": row.get("internal_status") or row.get("marketplace_status"), "needs_response": row.get("needs_response"), "last_message_at": parse_dt(row.get("last_message_at") or row.get("updated_at") or row.get("created_at")), "last_text": last_text, "media": media, "raw": raw})

    def chats(self, platform: str = "ALL", limit: int = 5000) -> dict[str, Any]:
        p = (platform or "ALL").upper()
        if "buyer_chats" not in self.tables():
            return {"ok": True, "items": [], "count": 0}
        try:
            rows = self.db.execute(text("SELECT * FROM buyer_chats WHERE (:p='ALL' OR platform=:p) ORDER BY COALESCE(last_message_at,updated_at,created_at) DESC,id DESC LIMIT :l"), {"p": p, "l": min(max(int(limit), 1), 20000)}).mappings().all()
        except Exception as exc:
            self.db.rollback()
            return {"ok": False, "items": [], "error": str(exc)}
        items = [self.normalize_chat(dict(r)) for r in rows]
        return {"ok": True, "platform": p, "count": len(items), "items": items}

    def chat_messages_by_internal_id(self, chat_id: int, limit: int = 1000) -> dict[str, Any]:
        try:
            chat = self.db.execute(text("SELECT * FROM buyer_chats WHERE id=:id"), {"id": chat_id}).mappings().first()
            if not chat:
                return {"chat": None, "items": [], "error": "chat not found"}
            return self.chat_messages(str(chat["platform"]), str(chat["external_chat_id"]), limit=limit, internal_chat=dict(chat))
        except Exception as exc:
            self.db.rollback()
            return {"chat": None, "items": [], "error": str(exc)}

    def chat_messages(self, platform: str, external_chat_id: str, limit: int = 1000, internal_chat: dict[str, Any] | None = None) -> dict[str, Any]:
        platform = (platform or "").upper()
        try:
            rows = self.db.execute(text("SELECT * FROM buyer_chat_messages WHERE platform=:p AND external_chat_id=:cid ORDER BY COALESCE(sent_at,created_at) ASC,id ASC LIMIT :l"), {"p": platform, "cid": external_chat_id, "l": min(max(int(limit), 1), 2000)}).mappings().all()
        except Exception as exc:
            self.db.rollback()
            return {"chat": None, "items": [], "error": str(exc)}
        items = []
        for r in rows:
            d = dict(r)
            raw = fix_tree(loads(d.get("raw")) or {})
            text_value = message_text_v56(raw) if is_bad_text(d.get("text")) else fix_text(d.get("text"))
            media = self.media_for("chat_message", d.get("external_message_id") or d.get("id"), platform, raw)
            items.append(fix_tree({"id": d.get("id"), "platform": platform, "external_message_id": d.get("external_message_id"), "direction": direction_from_raw(d, raw), "author_name": d.get("author_name") or get_any(raw, "author", "senderName", "name", default=None), "text": text_value, "sent_at": parse_dt(d.get("sent_at") or d.get("created_at")), "media": media, "raw": raw}))
        chat_norm = self.normalize_chat(internal_chat) if internal_chat else {"platform": platform, "technical_chat_id": external_chat_id}
        return {"chat": chat_norm, "items": items, "count": len(items)}

    def communications(self, entity_type: str = "review", platform: str = "ALL", limit: int = 20000) -> dict[str, Any]:
        p = (platform or "ALL").upper()
        et = entity_type
        rows_out = []
        if "recovery_communications" in self.tables():
            try:
                rows = self.db.execute(text("SELECT * FROM recovery_communications WHERE entity_type=:et AND (:p='ALL' OR platform=:p) ORDER BY COALESCE(created_at_marketplace,updated_at,created_at) DESC,id DESC LIMIT :l"), {"et": et, "p": p, "l": min(max(int(limit), 1), 50000)}).mappings().all()
            except Exception:
                self.db.rollback()
                rows = []
            for r in rows:
                d = dict(r)
                raw = fix_tree(loads(d.get("raw")) or {})
                ext = str(d.get("external_id") or d.get("id"))
                media = self.media_for(et, ext, d.get("platform") or p, raw)
                product = product_from_raw(d.get("platform") or p, d, raw)
                order = order_from_raw(d, raw)
                rows_out.append(fix_tree({**d, "raw": raw, "text": message_text_v56(raw) or fix_text(d.get("text")), "media": media, "product_name": product.get("product_name") or d.get("product_name"), "sku": product.get("sku") or d.get("sku"), "product_url": product.get("product_url"), "order_number": order.get("order_number") or d.get("order_number"), "order_url": order.get("order_url")}))
        if not rows_out:
            table_candidates = [("reviews", "review"), ("marketplace_reviews", "review")] if et == "review" else [("questions", "question"), ("marketplace_questions", "question")]
            for table_name, _etype in table_candidates:
                if table_name not in self.tables():
                    continue
                cols = self.cols(table_name)
                where = "WHERE (:p='ALL' OR platform=:p)" if "platform" in cols else ""
                try:
                    rows = self.db.execute(text(f"SELECT * FROM {table_name} {where} ORDER BY id DESC LIMIT :l"), {"p": p, "l": min(max(int(limit), 1), 50000)}).mappings().all()
                except Exception:
                    self.db.rollback()
                    rows = []
                for r in rows:
                    d = dict(r)
                    raw = fix_tree(loads(d.get("raw") or d.get("raw_payload")) or d)
                    ext = str(d.get("external_id") or d.get("external_review_id") or d.get("external_question_id") or d.get("id"))
                    media = self.media_for(et, ext, d.get("platform") or p, raw)
                    product = product_from_raw(d.get("platform") or p, d, raw)
                    order = order_from_raw(d, raw)
                    rows_out.append(fix_tree({"id": d.get("id"), "entity_type": et, "platform": d.get("platform") or p, "external_id": ext, "text": message_text_v56(raw) or fix_text(d.get("text") or d.get("question")), "rating": d.get("rating"), "created_at_marketplace": parse_dt(d.get("created_at_marketplace") or d.get("created_at")), "answer_text": d.get("answer_text") or d.get("response_text"), "media": media, "product_name": product.get("product_name"), "sku": product.get("sku"), "product_url": product.get("product_url"), "order_number": order.get("order_number"), "order_url": order.get("order_url"), "raw": raw}))
        return {"ok": True, "platform": p, "entity_type": et, "count": len(rows_out), "items": rows_out[:limit]}


    def sync_existing_media(self, limit: int = 100000) -> dict[str, Any]:
        cleaned = self.cleanup_false_media()
        totals: dict[str, int] = {}
        scanned = 0
        if "recovery_communications" in self.tables():
            try:
                rows = self.db.execute(text("SELECT entity_type, platform, external_id, raw FROM recovery_communications ORDER BY id DESC LIMIT :l"), {"l": min(max(int(limit), 1), 100000)}).mappings().all()
            except Exception:
                self.db.rollback()
                rows = []
            for r in rows:
                scanned += 1
                count = self.upsert_media(r["entity_type"], str(r["external_id"]), str(r["platform"]), r.get("raw"))
                totals[r["entity_type"]] = totals.get(r["entity_type"], 0) + count
        if "buyer_chats" in self.tables():
            try:
                rows = self.db.execute(text("SELECT platform, external_chat_id, raw FROM buyer_chats ORDER BY id DESC LIMIT :l"), {"l": min(max(int(limit), 1), 100000)}).mappings().all()
            except Exception:
                self.db.rollback()
                rows = []
            for r in rows:
                scanned += 1
                count = self.upsert_media("chat", str(r["external_chat_id"]), str(r["platform"]), r.get("raw"))
                totals["chat"] = totals.get("chat", 0) + count
        if "buyer_chat_messages" in self.tables():
            try:
                rows = self.db.execute(text("SELECT platform, external_message_id, raw FROM buyer_chat_messages ORDER BY id DESC LIMIT :l"), {"l": min(max(int(limit), 1), 100000)}).mappings().all()
            except Exception:
                self.db.rollback()
                rows = []
            for r in rows:
                scanned += 1
                count = self.upsert_media("chat_message", str(r["external_message_id"]), str(r["platform"]), r.get("raw"))
                totals["chat_message"] = totals.get("chat_message", 0) + count
        return {"ok": True, "scanned": scanned, "false_media_deleted": cleaned, "media_added_or_updated": totals}
