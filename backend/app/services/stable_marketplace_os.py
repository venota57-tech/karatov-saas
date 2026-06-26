from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import settings


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def h(value: Any) -> str:
    return hashlib.sha1(dumps(value).encode("utf-8")).hexdigest()[:32]


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 100000000000:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None
    raw = str(value).strip()
    if raw.isdigit():
        return parse_dt(int(raw))
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def get(obj: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj.get(key) not in (None, ""):
                return obj.get(key)
        for v in obj.values():
            found = get(v, *keys, default=None)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = get(v, *keys, default=None)
            if found not in (None, ""):
                return found
    return default


def items(data: Any, preferred: list[str] | None = None) -> list[dict[str, Any]]:
    preferred = preferred or []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    roots = [data]
    for k in ("result", "data"):
        if isinstance(data.get(k), (dict, list)):
            roots.insert(0, data[k])
    for root in roots:
        if isinstance(root, list):
            return [x for x in root if isinstance(x, dict)]
        if not isinstance(root, dict):
            continue
        for key in preferred + ["items", "chats", "events", "messages", "returns", "claims", "operations", "postings", "acts", "documents", "rows", "list", "result", "data"]:
            value = root.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                nested = items(value, preferred)
                if nested:
                    return nested
    return []


def media_from_raw(raw: Any) -> list[dict[str, Any]]:
    out, seen = [], set()
    raw = loads(raw) or raw
    for node in walk(raw):
        for key, value in node.items():
            lk = str(key).lower()
            if isinstance(value, str) and value.startswith("http") and any(x in lk for x in ["url", "link", "image", "photo", "video", "file", "media", "preview"]):
                kind = "image" if any(x in value.lower() for x in [".jpg", ".jpeg", ".png", ".webp", "image", "photo"]) else ("video" if any(x in value.lower() for x in [".mp4", ".mov", "video"]) else "file")
                sid = f"{kind}:{value}"
                if sid not in seen:
                    out.append({"media_type": kind, "url": value, "preview_url": value if kind == "image" else None, "filename": str(get(node, "name", "fileName", "filename", "title", default="") or "")})
                    seen.add(sid)
            if isinstance(value, list) and any(x in lk for x in ["attachments", "files", "images", "photos", "videos", "media"]):
                for it in value:
                    if not isinstance(it, dict):
                        continue
                    url = get(it, "url", "link", "src", "fileUrl", "file_url", "previewUrl", "preview", "downloadUrl", default=None)
                    download_id = get(it, "downloadID", "downloadId", "id", default=None)
                    kind = str(get(it, "contentType", "type", "mediaType", default="file") or "file").lower()
                    if "image" in kind or "photo" in kind:
                        kind = "image"
                    elif "video" in kind:
                        kind = "video"
                    else:
                        kind = "file"
                    if url or download_id:
                        sid = f"{kind}:{url or download_id}"
                        if sid not in seen:
                            out.append({"media_type": kind, "url": str(url) if url else None, "preview_url": str(url) if url and kind == "image" else None, "external_media_id": str(download_id) if download_id else None, "filename": str(get(it, "name", "fileName", "filename", "title", default="") or "")})
                            seen.add(sid)
    return out[:30]


def msg_text(obj: dict[str, Any]) -> str:
    direct = get(obj, "text", "message_text", "messageText", "body", "content", "value", "comment", "description", default=None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    m = obj.get("message") if isinstance(obj, dict) else None
    if isinstance(m, str) and m.strip():
        return m.strip()
    if isinstance(m, dict):
        return msg_text(m)
    return "[медиа]" if media_from_raw(obj) else ""


def msg_time(obj: dict[str, Any], fallback: Any = None) -> datetime | None:
    for k in ["sent_at", "sentAt", "created_at", "createdAt", "addTimestamp", "add_timestamp", "date", "timestamp", "time", "eventTime", "event_time", "lastMessageAt", "updatedAt"]:
        parsed = parse_dt(get(obj, k, default=None))
        if parsed:
            return parsed
    return parse_dt(fallback)


def direction(obj: dict[str, Any]) -> str:
    raw = " ".join(str(get(obj, k, default="") or "").lower() for k in ["direction", "sender", "author", "source", "user_type", "from", "owner", "type"])
    if any(x in raw for x in ["seller", "merchant", "operator", "support", "продав", "оператор"]):
        return "seller"
    return "customer"


class StableMarketplaceOS:
    def __init__(self, db: Session):
        self.db = db
        self.timeout = httpx.Timeout(float(os.getenv("STABLE_OS_REQUEST_TIMEOUT", "22")), connect=8)
        self.max_pages = int(os.getenv("STABLE_OS_MAX_PAGES", "3"))
        self.max_rows = int(os.getenv("STABLE_OS_MAX_ROWS", "120"))

    def _tables(self) -> set[str]:
        return set(inspect(self.db.bind).get_table_names())

    def _cols(self, table: str) -> set[str]:
        try:
            return {c["name"] for c in inspect(self.db.bind).get_columns(table)}
        except Exception:
            return set()

    def ensure_schema(self) -> None:
        dialect = self.db.bind.dialect.name
        id_type = "SERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
        bool_type = "BOOLEAN DEFAULT FALSE" if dialect == "postgresql" else "BOOLEAN DEFAULT 0"
        stmts = [
            f"""CREATE TABLE IF NOT EXISTS marketplace_raw_events (
                id {id_type}, platform VARCHAR(32), block VARCHAR(128), external_id VARCHAR(256),
                status VARCHAR(64), error TEXT, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS communication_media (
                id {id_type}, entity_type VARCHAR(64), entity_id VARCHAR(256), platform VARCHAR(32),
                external_media_id VARCHAR(256), media_type VARCHAR(32), url TEXT, preview_url TEXT,
                filename TEXT, mime_type VARCHAR(128), size_bytes INTEGER, source VARCHAR(64),
                visibility VARCHAR(64), send_status VARCHAR(64), content_base64 TEXT, raw_payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS buyer_chats (
                id {id_type}, platform VARCHAR(32), external_chat_id VARCHAR(256), chat_type VARCHAR(64),
                marketplace_status VARCHAR(128), internal_status VARCHAR(64) DEFAULT 'new',
                unread_count INTEGER DEFAULT 0, needs_response {bool_type}, buyer_name VARCHAR(256),
                buyer_id VARCHAR(128), order_number VARCHAR(256), posting_number VARCHAR(256), sku VARCHAR(128),
                product_name TEXT, product_url TEXT, product_image TEXT, assigned_to VARCHAR(128),
                operator_comment TEXT, reply_sign TEXT, first_customer_message_at TIMESTAMP,
                first_seller_response_at TIMESTAMP, last_message_at TIMESTAMP, response_minutes INTEGER,
                response_sla_status VARCHAR(64), raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS buyer_chat_messages (
                id {id_type}, platform VARCHAR(32), external_chat_id VARCHAR(256), external_message_id VARCHAR(256),
                direction VARCHAR(32), author_name VARCHAR(256), text TEXT, message_type VARCHAR(64),
                sent_at TIMESTAMP, media TEXT, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS buyer_returns (
                id {id_type}, platform VARCHAR(32), external_return_id VARCHAR(256), order_id VARCHAR(256),
                posting_number VARCHAR(256), sku VARCHAR(128), product_name TEXT, product_url TEXT,
                reason TEXT, marketplace_status VARCHAR(128), internal_status VARCHAR(64) DEFAULT 'new',
                assigned_to VARCHAR(128), operator_comment TEXT, amount VARCHAR(64), quantity INTEGER,
                created_at_marketplace TIMESTAMP, updated_at_marketplace TIMESTAMP, raw TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS marketplace_operations (
                id {id_type}, platform VARCHAR(32), operation_type VARCHAR(64), external_id VARCHAR(256),
                document_number VARCHAR(256), sku VARCHAR(128), product_name TEXT, warehouse TEXT,
                amount VARCHAR(64), quantity INTEGER, reason TEXT, status VARCHAR(64),
                marketplace_status VARCHAR(128), cx_workflow_status VARCHAR(64), responsible VARCHAR(128),
                comment TEXT, raw TEXT, occurred_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        ]
        for stmt in stmts:
            self.db.execute(text(stmt))
        for table, cols in {
            "buyer_chats": [("buyer_id","VARCHAR(128)"),("order_number","VARCHAR(256)"),("posting_number","VARCHAR(256)"),("product_url","TEXT"),("product_image","TEXT"),("reply_sign","TEXT")],
            "buyer_chat_messages": [("media","TEXT")],
            "buyer_returns": [("product_url","TEXT"),("assigned_to","VARCHAR(128)"),("operator_comment","TEXT")],
            "communication_media": [("send_status","VARCHAR(64)"),("content_base64","TEXT")],
        }.items():
            existing = self._cols(table)
            for col, typ in cols:
                if col not in existing:
                    try:
                        self.db.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                    except Exception:
                        pass
        for stmt in [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_comm_media_entity_external ON communication_media(entity_type, entity_id, platform, external_media_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_buyer_chats ON buyer_chats(platform, external_chat_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_buyer_chat_messages ON buyer_chat_messages(platform, external_message_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_buyer_returns ON buyer_returns(platform, external_return_id)",
            "CREATE INDEX IF NOT EXISTS ix_raw_events_date ON marketplace_raw_events(created_at)",
        ]:
            try:
                self.db.execute(text(stmt))
            except Exception:
                pass
        self.db.commit()

    def raw(self, platform: str, block: str, status: str, raw: Any, error: str | None = None, external_id: str | None = None) -> None:
        self.ensure_schema()
        self.db.execute(text("""INSERT INTO marketplace_raw_events(platform, block, external_id, status, error, raw, created_at, updated_at)
                                VALUES (:p,:b,:e,:s,:err,:r,:c,:u)"""),
                        {"p": platform, "b": block, "e": external_id, "s": status, "err": error, "r": dumps(raw), "c": now_utc(), "u": now_utc()})
        self.db.commit()

    async def req(self, platform: str, block: str, method: str, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> tuple[bool, Any, str | None]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(method, url, headers=headers, params=params, json=body)
            try:
                data = resp.json()
            except Exception:
                data = {"text": resp.text}
            if resp.status_code >= 400:
                err = f"HTTP {resp.status_code}: {str(data)[:900]}"
                self.raw(platform, block, "failed", {"url": url, "params": params, "body": body, "response": data}, err)
                return False, data, err
            self.raw(platform, block, "success", {"url": url, "params": params, "body": body, "response": data})
            return True, data, None
        except Exception as exc:
            err = str(exc)
            self.raw(platform, block, "failed", {"url": url, "params": params, "body": body}, err)
            return False, None, err

    def media_upsert(self, entity_type: str, entity_id: str, platform: str, media: list[dict[str, Any]], source: str = "marketplace") -> int:
        count = 0
        for m in media or []:
            ext = str(m.get("external_media_id") or m.get("url") or h(m))
            params = {
                "entity_type": entity_type, "entity_id": str(entity_id), "platform": platform,
                "external_media_id": ext, "media_type": m.get("media_type") or m.get("type") or "file",
                "url": m.get("url"), "preview_url": m.get("preview_url"), "filename": m.get("filename"),
                "mime_type": m.get("mime_type"), "size_bytes": m.get("size_bytes"), "source": source,
                "visibility": m.get("visibility") or ("marketplace_visible" if source == "marketplace" else "internal_only"),
                "send_status": m.get("send_status") or "saved", "content_base64": m.get("content_base64"),
                "raw_payload": dumps(m), "updated_at": now_utc(), "created_at": now_utc()
            }
            existing = self.db.execute(text("""SELECT id FROM communication_media WHERE entity_type=:entity_type AND entity_id=:entity_id AND platform=:platform AND external_media_id=:external_media_id"""), params).first()
            if existing:
                self.db.execute(text("""UPDATE communication_media SET media_type=:media_type, url=COALESCE(:url,url), preview_url=COALESCE(:preview_url,preview_url),
                                        filename=COALESCE(:filename,filename), mime_type=COALESCE(:mime_type,mime_type), size_bytes=COALESCE(:size_bytes,size_bytes),
                                        source=:source, visibility=:visibility, send_status=:send_status, content_base64=COALESCE(:content_base64,content_base64),
                                        raw_payload=:raw_payload, updated_at=:updated_at WHERE id=:id"""), {**params, "id": existing[0]})
            else:
                self.db.execute(text("""INSERT INTO communication_media(entity_type, entity_id, platform, external_media_id, media_type, url, preview_url, filename, mime_type, size_bytes, source, visibility, send_status, content_base64, raw_payload, created_at, updated_at)
                                        VALUES (:entity_type,:entity_id,:platform,:external_media_id,:media_type,:url,:preview_url,:filename,:mime_type,:size_bytes,:source,:visibility,:send_status,:content_base64,:raw_payload,:created_at,:updated_at)"""), params)
            count += 1
        self.db.commit()
        return count

    def upsert_chat(self, platform: str, obj: dict[str, Any]) -> str:
        cid = str(get(obj, "chatID", "chatId", "chat_id", "id", default="") or h(obj))
        good = get(obj, "goodCard", "product", "productDetails", default={}) or {}
        sku = str(get(obj, "nmID", "nmId", "sku", "offer_id", "product_id", default="") or get(good, "nmID", "nmId", "sku", default="") or "")
        last = obj.get("lastMessage") if isinstance(obj.get("lastMessage"), dict) else {}
        media = media_from_raw(obj)
        img = next((m.get("preview_url") or m.get("url") for m in media if m.get("media_type") == "image"), None)
        row = {
            "platform": platform, "external_chat_id": cid,
            "chat_type": str(get(obj, "type", "chat_type", default="buyer") or "buyer"),
            "marketplace_status": str(get(obj, "status", "state", default="") or ""),
            "unread_count": int(get(obj, "unread_count", "unreadCount", default=0) or 0),
            "needs_response": bool(get(obj, "needAnswer", "needs_response", "unanswered", default=False) or int(get(obj, "unread_count", "unreadCount", default=0) or 0) > 0),
            "buyer_name": str(get(obj, "clientName", "buyer_name", "buyerName", "customer_name", default="") or ""),
            "buyer_id": str(get(obj, "clientID", "buyerId", "customerId", default="") or ""),
            "order_number": str(get(obj, "orderNumber", "order_id", "rid", "srid", default="") or get(good, "rid", default="") or ""),
            "posting_number": str(get(obj, "postingNumber", "posting_number", default="") or ""),
            "sku": sku,
            "product_name": str(get(obj, "productName", "product_name", "subject", "title", default="") or ""),
            "product_url": f"https://www.wildberries.ru/catalog/{sku}/detail.aspx" if platform == "WB" and sku.isdigit() else None,
            "product_image": img,
            "reply_sign": str(get(obj, "replySign", "reply_sign", default="") or ""),
            "last_message_at": msg_time(obj) or msg_time(last),
            "raw": dumps(obj), "updated_at": now_utc(), "created_at": now_utc()
        }
        exists = self.db.execute(text("SELECT id FROM buyer_chats WHERE platform=:platform AND external_chat_id=:external_chat_id"), row).first()
        if exists:
            self.db.execute(text("""UPDATE buyer_chats SET marketplace_status=:marketplace_status, unread_count=:unread_count, needs_response=:needs_response,
                                    buyer_name=COALESCE(NULLIF(:buyer_name,''),buyer_name), buyer_id=COALESCE(NULLIF(:buyer_id,''),buyer_id),
                                    order_number=COALESCE(NULLIF(:order_number,''),order_number), posting_number=COALESCE(NULLIF(:posting_number,''),posting_number),
                                    sku=COALESCE(NULLIF(:sku,''),sku), product_name=COALESCE(NULLIF(:product_name,''),product_name),
                                    product_url=COALESCE(:product_url,product_url), product_image=COALESCE(:product_image,product_image),
                                    reply_sign=COALESCE(NULLIF(:reply_sign,''),reply_sign), last_message_at=COALESCE(:last_message_at,last_message_at),
                                    raw=:raw, updated_at=:updated_at WHERE platform=:platform AND external_chat_id=:external_chat_id"""), row)
        else:
            self.db.execute(text("""INSERT INTO buyer_chats(platform,external_chat_id,chat_type,marketplace_status,internal_status,unread_count,needs_response,buyer_name,buyer_id,order_number,posting_number,sku,product_name,product_url,product_image,reply_sign,last_message_at,raw,created_at,updated_at)
                                    VALUES (:platform,:external_chat_id,:chat_type,:marketplace_status,'new',:unread_count,:needs_response,:buyer_name,:buyer_id,:order_number,:posting_number,:sku,:product_name,:product_url,:product_image,:reply_sign,:last_message_at,:raw,:created_at,:updated_at)"""), row)
        self.media_upsert("chat", cid, platform, media)
        return cid

    def upsert_message(self, platform: str, chat_id: str, obj: dict[str, Any]) -> str:
        mid = str(get(obj, "eventID", "eventId", "messageId", "message_id", "id", default="") or f"{chat_id}:{h(obj)}")
        m = media_from_raw(obj)
        row = {
            "platform": platform, "external_chat_id": chat_id, "external_message_id": mid,
            "direction": direction(obj), "author_name": str(get(obj, "author", "name", "senderName", default="") or ""),
            "text": msg_text(obj), "message_type": str(get(obj, "eventType", "type", default="message") or "message"),
            "sent_at": msg_time(obj), "media": dumps(m), "raw": dumps(obj), "created_at": now_utc(), "updated_at": now_utc()
        }
        exists = self.db.execute(text("SELECT id FROM buyer_chat_messages WHERE platform=:platform AND external_message_id=:external_message_id"), row).first()
        if exists:
            self.db.execute(text("""UPDATE buyer_chat_messages SET direction=:direction, author_name=:author_name, text=COALESCE(NULLIF(:text,''),text),
                                    message_type=:message_type, sent_at=COALESCE(:sent_at,sent_at), media=:media, raw=:raw, updated_at=:updated_at
                                    WHERE platform=:platform AND external_message_id=:external_message_id"""), row)
        else:
            self.db.execute(text("""INSERT INTO buyer_chat_messages(platform,external_chat_id,external_message_id,direction,author_name,text,message_type,sent_at,media,raw,created_at,updated_at)
                                    VALUES (:platform,:external_chat_id,:external_message_id,:direction,:author_name,:text,:message_type,:sent_at,:media,:raw,:created_at,:updated_at)"""), row)
        self.media_upsert("chat_message", mid, platform, m)
        return mid

    def upsert_return(self, platform: str, obj: dict[str, Any]) -> str:
        rid = str(get(obj, "id", "claimID", "claimId", "return_id", "returnId", "posting_number", "postingNumber", "srid", default="") or h(obj))
        sku = str(get(obj, "nmID", "nmId", "sku", "offer_id", "product_id", default="") or "")
        row = {
            "platform": platform, "external_return_id": rid, "order_id": str(get(obj, "order_id", "orderId", "rid", "srid", default="") or ""),
            "posting_number": str(get(obj, "posting_number", "postingNumber", default="") or ""), "sku": sku,
            "product_name": str(get(obj, "productName", "product_name", "subject", "name", default="") or ""),
            "product_url": f"https://www.wildberries.ru/catalog/{sku}/detail.aspx" if platform == "WB" and sku.isdigit() else None,
            "reason": str(get(obj, "reason", "returnReason", "comment", "description", default="") or ""),
            "marketplace_status": str(get(obj, "status", "state", default="") or ""),
            "amount": str(get(obj, "amount", "price", "total", default="") or ""),
            "quantity": int(get(obj, "quantity", "qty", "count", default=1) or 1),
            "created_at_marketplace": msg_time(obj, get(obj, "created_at", "createdAt", "returnDate", default=None)),
            "updated_at_marketplace": msg_time(obj, get(obj, "updated_at", "updatedAt", default=None)),
            "raw": dumps(obj), "created_at": now_utc(), "updated_at": now_utc()
        }
        exists = self.db.execute(text("SELECT id FROM buyer_returns WHERE platform=:platform AND external_return_id=:external_return_id"), row).first()
        if exists:
            self.db.execute(text("""UPDATE buyer_returns SET order_id=:order_id, posting_number=:posting_number, sku=:sku, product_name=:product_name,
                                    product_url=COALESCE(:product_url,product_url), reason=:reason, marketplace_status=:marketplace_status, amount=:amount,
                                    quantity=:quantity, created_at_marketplace=COALESCE(:created_at_marketplace,created_at_marketplace),
                                    updated_at_marketplace=COALESCE(:updated_at_marketplace,updated_at_marketplace), raw=:raw, updated_at=:updated_at
                                    WHERE platform=:platform AND external_return_id=:external_return_id"""), row)
        else:
            self.db.execute(text("""INSERT INTO buyer_returns(platform,external_return_id,order_id,posting_number,sku,product_name,product_url,reason,marketplace_status,internal_status,amount,quantity,created_at_marketplace,updated_at_marketplace,raw,created_at,updated_at)
                                    VALUES (:platform,:external_return_id,:order_id,:posting_number,:sku,:product_name,:product_url,:reason,:marketplace_status,'new',:amount,:quantity,:created_at_marketplace,:updated_at_marketplace,:raw,:created_at,:updated_at)"""), row)
        self.media_upsert("return_request", rid, platform, media_from_raw(obj))
        self.upsert_operation(platform, "return_request", rid, obj)
        return rid

    def upsert_operation(self, platform: str, op_type: str, ext: str, obj: dict[str, Any]) -> None:
        sku = str(get(obj, "nmID", "nmId", "sku", "offer_id", "product_id", default="") or "")
        row = {
            "platform": platform, "operation_type": op_type, "external_id": ext,
            "document_number": str(get(obj, "number", "act_id", "actId", "posting_number", "postingNumber", "claimID", "returnId", default="") or ext),
            "sku": sku or None, "product_name": get(obj, "productName", "product_name", "subject", "name", "title", default=None),
            "warehouse": get(obj, "warehouseName", "warehouse", "delivery_method_name", default=None),
            "amount": str(get(obj, "amount", "price", "total", "sum", default="") or "") or None,
            "quantity": int(get(obj, "quantity", "qty", "count", default=1) or 1),
            "reason": get(obj, "reason", "returnReason", "status", "state", "category", default=None),
            "status": "synced", "marketplace_status": str(get(obj, "status", "state", default="synced") or "synced"),
            "cx_workflow_status": "new_to_review", "raw": dumps(obj), "occurred_at": msg_time(obj) or now_utc(),
            "created_at": now_utc(), "updated_at": now_utc()
        }
        exists = self.db.execute(text("SELECT id FROM marketplace_operations WHERE platform=:platform AND operation_type=:operation_type AND external_id=:external_id"), row).first()
        if exists:
            self.db.execute(text("""UPDATE marketplace_operations SET document_number=:document_number, sku=:sku, product_name=:product_name, warehouse=:warehouse,
                                    amount=:amount, quantity=:quantity, reason=:reason, status=:status, marketplace_status=:marketplace_status,
                                    raw=:raw, occurred_at=COALESCE(:occurred_at,occurred_at), updated_at=:updated_at WHERE id=:id"""), {**row, "id": exists[0]})
        else:
            self.db.execute(text("""INSERT INTO marketplace_operations(platform,operation_type,external_id,document_number,sku,product_name,warehouse,amount,quantity,reason,status,marketplace_status,cx_workflow_status,raw,occurred_at,created_at,updated_at)
                                    VALUES (:platform,:operation_type,:external_id,:document_number,:sku,:product_name,:warehouse,:amount,:quantity,:reason,:status,:marketplace_status,:cx_workflow_status,:raw,:occurred_at,:created_at,:updated_at)"""), row)
        self.db.commit()

    async def sync_wb_customer_ops(self) -> dict[str, Any]:
        token = getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")
        res = {"platform": "WB", "block": "customer_ops", "received": 0, "created": 0, "updated": 0, "errors": []}
        if not token:
            res["errors"].append("WB token missing")
            self.raw("WB", "customer_ops", "failed", {}, "WB token missing")
            return res
        headers = {"Authorization": token}

        ok, data, err = await self.req("WB", "chats_list", "GET", "https://buyer-chat-api.wildberries.ru/api/v1/seller/chats", headers=headers)
        if ok:
            chats = items(data, ["result", "chats"])
            res["received"] += len(chats)
            for c in chats[: self.max_rows]:
                cid = self.upsert_chat("WB", c)
                last = c.get("lastMessage") if isinstance(c.get("lastMessage"), dict) else None
                if last:
                    self.upsert_message("WB", cid, {**last, "chatID": cid})
        else:
            res["errors"].append(err)

        # Correct WB chat events contract: first call can be without next; no limit parameter.
        next_value = None
        for _ in range(self.max_pages):
            params = {"next": next_value} if next_value else None
            ok, data, err = await self.req("WB", "chat_events", "GET", "https://buyer-chat-api.wildberries.ru/api/v1/seller/events", headers=headers, params=params)
            if not ok:
                res["errors"].append(err)
                break
            root = loads(data) or data
            got = items(root, ["events"])
            res["received"] += len(got)
            for ev in got[: self.max_rows]:
                cid = str(get(ev, "chatID", "chatId", "chat_id", default="") or "")
                if not cid:
                    continue
                self.upsert_chat("WB", {"chatID": cid, "lastMessage": ev.get("message") if isinstance(ev.get("message"), dict) else ev})
                self.upsert_message("WB", cid, ev.get("message") if isinstance(ev.get("message"), dict) else ev)
            total = get(root, "totalEvents", default=0) or 0
            next_value = get(root, "next", default=None)
            if not next_value or int(total or 0) == 0:
                break
            await asyncio.sleep(1.1)

        ok, data, err = await self.req("WB", "returns_claims", "GET", "https://returns-api.wildberries.ru/api/v1/claims", headers=headers)
        if ok:
            claims = items(data, ["claims"])
            res["received"] += len(claims)
            for claim in claims[: self.max_rows]:
                self.upsert_return("WB", claim)
        else:
            res["errors"].append(err)
        self.db.commit()
        return res

    async def sync_ozon_customer_ops(self) -> dict[str, Any]:
        cid, key = getattr(settings, "ozon_client_id", ""), getattr(settings, "ozon_api_key", "")
        res = {"platform": "OZON", "block": "customer_ops", "received": 0, "created": 0, "updated": 0, "errors": []}
        if not cid or not key:
            res["errors"].append("Ozon credentials missing")
            self.raw("OZON", "customer_ops", "failed", {}, "Ozon credentials missing")
            return res
        headers = {"Client-Id": cid, "Api-Key": key, "Content-Type": "application/json"}

        ok, data, err = await self.req("OZON", "chats_list", "POST", "https://api-seller.ozon.ru/v3/chat/list", headers=headers, body={"filter": {}, "limit": min(self.max_rows, 100), "offset": 0})
        if ok:
            chats = items(data, ["chats"])
            res["received"] += len(chats)
            for c in chats[: self.max_rows]:
                chat_id = self.upsert_chat("OZON", c)
                ok2, hist, herr = await self.req("OZON", "chat_history", "POST", "https://api-seller.ozon.ru/v3/chat/history", headers=headers, body={"chat_id": chat_id, "limit": min(self.max_rows, 100)})
                if ok2:
                    for msg in items(hist, ["messages"]):
                        self.upsert_message("OZON", chat_id, msg)
                else:
                    res["errors"].append(herr)
        else:
            res["errors"].append(err)

        # Ozon returns: try current candidate first; if API returns "obsolete", record once and stop spamming.
        for base in ["/v4/returns/company/fbs", "/v4/returns/company/fbo", "/v3/returns/company/fbs", "/v3/returns/company/fbo"]:
            last_id: int | str = 0
            for _ in range(self.max_pages):
                ok, data, err = await self.req("OZON", f"returns{base}", "POST", f"https://api-seller.ozon.ru{base}", headers=headers, body={"filter": {}, "last_id": last_id, "limit": min(self.max_rows, 100)})
                if not ok:
                    if "obsolete method" in str(err).lower():
                        break
                    res["errors"].append(err)
                    break
                got = items(data, ["returns"])
                res["received"] += len(got)
                for r in got:
                    self.upsert_return("OZON", r)
                nxt = get(data, "last_id", "lastId", default=None)
                if not got or not nxt or str(nxt) == str(last_id):
                    break
                last_id = nxt
                await asyncio.sleep(0.5)
        self.db.commit()
        return res

    async def sync_operations(self, platform: str = "ALL") -> dict[str, Any]:
        platform = (platform or "ALL").upper()
        results = []
        if platform in ("ALL", "OZON"):
            results.append(await self.sync_ozon_operations())
        if platform in ("ALL", "WB"):
            results.append(await self.sync_wb_operations())
        return {"ok": any(r.get("ok", True) for r in results), "platform": platform, "results": results}

    async def sync_customer_ops(self, platform: str = "ALL") -> dict[str, Any]:
        platform = (platform or "ALL").upper()
        results = []
        if platform in ("ALL", "OZON"):
            results.append(await self.sync_ozon_customer_ops())
        if platform in ("ALL", "WB"):
            results.append(await self.sync_wb_customer_ops())
        return {"ok": any(not r.get("errors") for r in results), "platform": platform, "results": results}

    async def sync_ozon_operations(self) -> dict[str, Any]:
        cid, key = getattr(settings, "ozon_client_id", ""), getattr(settings, "ozon_api_key", "")
        res = {"platform": "OZON", "block": "operations", "received": 0, "errors": []}
        if not cid or not key:
            res["errors"].append("Ozon credentials missing")
            return res
        headers = {"Client-Id": cid, "Api-Key": key, "Content-Type": "application/json"}
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=31)

        # Ozon told us the legal upper bound for FbsActListRequest.Limit is 50.
        cursor = date_from
        while cursor < date_to:
            to = min(cursor + timedelta(days=7), date_to)
            ok, data, err = await self.req("OZON", "acts_fbs_list", "POST", "https://api-seller.ozon.ru/v2/posting/fbs/act/list", headers=headers, body={"filter": {"date_from": cursor.isoformat(), "date_to": to.isoformat()}, "limit": 50})
            if ok:
                got = items(data, ["acts"])
                res["received"] += len(got)
                for act in got:
                    self.upsert_operation("OZON", "act", str(get(act, "id", "act_id", "actId", "number", default="") or h(act)), act)
            else:
                res["errors"].append(err)
            cursor = to
            await asyncio.sleep(0.5)

        ok, data, err = await self.req("OZON", "postings_fbs_list", "POST", "https://api-seller.ozon.ru/v3/posting/fbs/list", headers=headers, body={"dir": "DESC", "filter": {"since": date_from.isoformat(), "to": date_to.isoformat()}, "limit": 100, "offset": 0, "with": {"analytics_data": True, "financial_data": False}})
        if ok:
            got = items(data, ["postings"])
            res["received"] += len(got)
            for post in got:
                status = str(get(post, "status", default="") or "")
                op_type = "shipment_issue" if status in {"awaiting_packaging", "awaiting_deliver", "arbitration", "cancelled", "delivering"} else "posting"
                self.upsert_operation("OZON", op_type, str(get(post, "posting_number", "postingNumber", default="") or h(post)), post)
        else:
            res["errors"].append(err)
        return res

    async def sync_wb_operations(self) -> dict[str, Any]:
        token = getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")
        res = {"platform": "WB", "block": "operations", "received": 0, "errors": []}
        if not token:
            res["errors"].append("WB token missing")
            return res
        headers = {"Authorization": token}
        date_to = datetime.now(timezone.utc).date()
        date_from = date_to - timedelta(days=31)

        for block, url, params in [
            ("goods_return", "https://seller-analytics-api.wildberries.ru/api/v1/analytics/goods-return", {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()}),
            ("documents_categories", "https://documents-api.wildberries.ru/api/v1/documents/categories", {"locale": "ru"}),
            ("documents_list", "https://documents-api.wildberries.ru/api/v1/documents/list", {"locale": "ru", "beginTime": date_from.isoformat(), "endTime": date_to.isoformat(), "limit": 50, "offset": 0}),
        ]:
            ok, data, err = await self.req("WB", block, "GET", url, headers=headers, params=params)
            if not ok:
                res["errors"].append(err)
                continue
            got = items(data, ["documents", "categories", "returns", "items"])
            res["received"] += len(got)
            for obj in got[: self.max_rows]:
                raw = dumps(obj).lower()
                if any(x in raw for x in ["обезлич", "anonym"]):
                    typ = "anonymized_item"
                elif any(x in raw for x in ["излиш", "surplus", "excess"]):
                    typ = "surplus"
                elif any(x in raw for x in ["недостач", "shortage"]):
                    typ = "shortage"
                elif any(x in raw for x in ["расхожд", "discrep"]):
                    typ = "discrepancy"
                elif "акт" in raw or "act" in raw:
                    typ = "act"
                elif block == "goods_return":
                    typ = "return"
                else:
                    typ = "document"
                self.upsert_operation("WB", typ, str(get(obj, "id", "serviceName", "name", "title", "docNumber", default="") or h(obj)), obj)
            await asyncio.sleep(1.1)
        return res

    def _media_for(self, entity_type: str, entity_id: str, platform: str) -> list[dict[str, Any]]:
        rows = self.db.execute(text("""SELECT id, media_type, url, preview_url, filename, mime_type, size_bytes, source, visibility, send_status, created_at
                                       FROM communication_media WHERE entity_type=:t AND entity_id=:e AND platform=:p ORDER BY id DESC"""),
                               {"t": entity_type, "e": str(entity_id), "p": platform}).mappings().all()
        return [self._decode(dict(r)) for r in rows]

    def _decode(self, row: dict[str, Any]) -> dict[str, Any]:
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
        for k in ("raw", "media", "raw_payload"):
            if k in row:
                row[k] = loads(row.get(k))
        return row

    def communications(self, platform: str = "ALL", limit: int = 100) -> dict[str, Any]:
        self.ensure_schema()
        platform = (platform or "ALL").upper()
        limit = min(max(int(limit or 100), 1), 500)
        out: list[dict[str, Any]] = []

        # reviews/questions via ORM if models exist
        try:
            from app.models import Review, Question
            q = self.db.query(Review)
            if platform != "ALL":
                q = q.filter(Review.platform == platform)
            for r in q.order_by(Review.created_at_marketplace.desc().nullslast(), Review.id.desc()).limit(limit).all():
                raw = getattr(r, "raw", None) or getattr(r, "raw_payload", None)
                media = media_from_raw(raw)
                self.media_upsert("review", str(getattr(r, "external_id", r.id)), r.platform, media)
                out.append({"entity_type": "review", "id": r.id, "external_id": getattr(r, "external_id", None), "platform": r.platform, "created_at": getattr(r, "created_at_marketplace", None), "product_name": getattr(r, "product_name", None), "sku": getattr(r, "sku", None), "client_name": None, "text": getattr(r, "text", None), "rating": getattr(r, "rating", None), "media": media + self._media_for("review", str(getattr(r, "external_id", r.id)), r.platform)})
            q2 = self.db.query(Question)
            if platform != "ALL":
                q2 = q2.filter(Question.platform == platform)
            for r in q2.order_by(Question.created_at_marketplace.desc().nullslast(), Question.id.desc()).limit(limit).all():
                raw = getattr(r, "raw", None) or getattr(r, "raw_payload", None)
                media = media_from_raw(raw)
                self.media_upsert("question", str(getattr(r, "external_id", r.id)), r.platform, media)
                out.append({"entity_type": "question", "id": r.id, "external_id": getattr(r, "external_id", None), "platform": r.platform, "created_at": getattr(r, "created_at_marketplace", None), "product_name": getattr(r, "product_name", None), "sku": getattr(r, "sku", None), "client_name": None, "text": getattr(r, "text", None), "rating": None, "media": media + self._media_for("question", str(getattr(r, "external_id", r.id)), r.platform)})
        except Exception as exc:
            self.raw(platform, "communications_orm", "failed", {}, str(exc))

        for table, entity in [("buyer_chats", "chat"), ("buyer_returns", "return_request")]:
            if table not in self._tables():
                continue
            rows = self.db.execute(text(f"SELECT * FROM {table} WHERE (:p='ALL' OR platform=:p) ORDER BY COALESCE(last_message_at, created_at_marketplace, updated_at, created_at) DESC, id DESC LIMIT :l"), {"p": platform, "l": limit}).mappings().all()
            for rr in rows:
                r = self._decode(dict(rr))
                eid = str(r.get("external_chat_id") or r.get("external_return_id") or r.get("id"))
                media = media_from_raw(r.get("raw")) + self._media_for(entity, eid, r.get("platform"))
                out.append({"entity_type": entity, "id": r.get("id"), "external_id": eid, "platform": r.get("platform"), "created_at": r.get("last_message_at") or r.get("created_at_marketplace") or r.get("created_at"), "product_name": r.get("product_name"), "sku": r.get("sku"), "client_name": r.get("buyer_name"), "order_number": r.get("order_number") or r.get("posting_number") or r.get("order_id"), "text": r.get("operator_comment") or r.get("reason") or r.get("marketplace_status") or "", "status": r.get("internal_status"), "media": media, "raw": r.get("raw")})
        out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return {"ok": True, "platform": platform, "items": [self._decode(x) for x in out[:limit]]}

    def operations(self, platform: str = "ALL", limit: int = 200) -> dict[str, Any]:
        self.ensure_schema()
        rows = self.db.execute(text("""SELECT * FROM marketplace_operations WHERE (:p='ALL' OR platform=:p)
                                       ORDER BY COALESCE(occurred_at, updated_at, created_at) DESC, id DESC LIMIT :l"""),
                               {"p": (platform or "ALL").upper(), "l": min(max(int(limit or 200), 1), 500)}).mappings().all()
        return {"ok": True, "platform": (platform or "ALL").upper(), "items": [self._decode(dict(r)) for r in rows]}

    def diagnostics(self, platform: str = "ALL", limit: int = 120) -> dict[str, Any]:
        self.ensure_schema()
        rows = self.db.execute(text("""SELECT platform, block, status, error, created_at FROM marketplace_raw_events
                                       WHERE (:p='ALL' OR platform=:p) ORDER BY created_at DESC LIMIT :l"""),
                               {"p": (platform or "ALL").upper(), "l": min(max(int(limit or 120), 1), 500)}).mappings().all()
        return {"ok": True, "platform": (platform or "ALL").upper(), "items": [self._decode(dict(r)) for r in rows]}

    def upload_media_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_schema()
        entity_type = str(payload.get("entity_type") or "internal")
        entity_id = str(payload.get("entity_id") or "manual")
        platform = str(payload.get("platform") or "ALL").upper()
        raw_b64 = str(payload.get("content_base64") or "")
        if "," in raw_b64 and raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        filename = str(payload.get("filename") or "attachment")
        mime = str(payload.get("mime_type") or "application/octet-stream")
        try:
            size = len(base64.b64decode(raw_b64.encode("utf-8"))) if raw_b64 else 0
        except Exception:
            size = 0
        media = [{"external_media_id": f"cxhub:{h({'name': filename, 'b': raw_b64[:120]})}", "media_type": "image" if mime.startswith("image/") else ("video" if mime.startswith("video/") else "file"), "filename": filename, "mime_type": mime, "size_bytes": size, "content_base64": raw_b64, "source": "cx_hub_upload", "visibility": "internal_only", "send_status": "saved_internal"}]
        self.media_upsert(entity_type, entity_id, platform, media, source="cx_hub_upload")
        return {"ok": True, "message": "Файл сохранен во внутренние вложения CX Hub. Отправка в маркетплейс выполняется только для тех методов, где API явно поддерживает вложения.", "media": media}
