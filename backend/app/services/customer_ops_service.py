from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in (None, ""):
                return value
        for value in obj.values():
            found = _get(value, *keys, default=None)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _get(value, *keys, default=None)
            if found not in (None, ""):
                return found
    return default


def _extract_items(data: Any, preferred: list[str] | None = None) -> list[dict[str, Any]]:
    preferred = preferred or []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    roots = [data]
    if isinstance(data.get("result"), (dict, list)):
        roots.insert(0, data["result"])
    for root in roots:
        if isinstance(root, list):
            return [x for x in root if isinstance(x, dict)]
        if not isinstance(root, dict):
            continue
        for key in preferred + ["items", "chats", "events", "messages", "returns", "claims", "operations", "postings", "list", "data", "rows", "result"]:
            value = root.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                nested = _extract_items(value, preferred)
                if nested:
                    return nested
    return []


def _short_hash(value: Any) -> str:
    return hashlib.sha1(_json_dumps(value).encode("utf-8")).hexdigest()[:24]


def _direction(item: dict[str, Any]) -> str:
    raw = str(_get(item, "direction", "sender", "author", "source", "type", default="")).lower()
    if any(x in raw for x in ["seller", "merchant", "operator", "support", "продав", "оператор"]):
        return "seller"
    if item.get("isSeller") is True or item.get("is_seller") is True or item.get("fromSeller") is True:
        return "seller"
    return "customer"


class CustomerOpsService:
    """Real ingestion for buyer chats and return requests. Never creates demo rows."""

    def __init__(self, db: Session):
        self.db = db
        self.timeout = httpx.Timeout(12.0, connect=6.0)
        self.max_chats = int(os.getenv("CUSTOMER_OPS_MAX_CHATS", "40") or "40")
        self.max_messages = int(os.getenv("CUSTOMER_OPS_MAX_MESSAGES_PER_CHAT", "50") or "50")
        self.max_returns = int(os.getenv("CUSTOMER_OPS_MAX_RETURNS", "80") or "80")

    def ensure_schema(self) -> None:
        dialect = self.db.bind.dialect.name
        id_type = "SERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
        bool_type = "BOOLEAN DEFAULT FALSE" if dialect == "postgresql" else "BOOLEAN DEFAULT 0"
        ddl = [
            f"""
            CREATE TABLE IF NOT EXISTS marketplace_raw_events (
              id {id_type}, platform VARCHAR(32) NOT NULL, block VARCHAR(128) NOT NULL,
              external_id VARCHAR(256), status VARCHAR(64) DEFAULT 'received', error TEXT,
              raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS buyer_chats (
              id {id_type}, platform VARCHAR(32) NOT NULL, external_chat_id VARCHAR(256) NOT NULL,
              chat_type VARCHAR(64), marketplace_status VARCHAR(128), internal_status VARCHAR(64) DEFAULT 'new',
              unread_count INTEGER DEFAULT 0, needs_response {bool_type}, buyer_name VARCHAR(256), sku VARCHAR(128),
              product_name TEXT, assigned_to VARCHAR(128), operator_comment TEXT,
              first_customer_message_at TIMESTAMP, first_seller_response_at TIMESTAMP, last_message_at TIMESTAMP,
              response_minutes INTEGER, response_sla_status VARCHAR(64), raw TEXT,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS buyer_chat_messages (
              id {id_type}, platform VARCHAR(32) NOT NULL, external_chat_id VARCHAR(256) NOT NULL,
              external_message_id VARCHAR(256) NOT NULL, direction VARCHAR(32) DEFAULT 'customer', author_name VARCHAR(256),
              text TEXT, message_type VARCHAR(64), sent_at TIMESTAMP, raw TEXT,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS buyer_returns (
              id {id_type}, platform VARCHAR(32) NOT NULL, external_return_id VARCHAR(256) NOT NULL,
              order_id VARCHAR(256), posting_number VARCHAR(256), sku VARCHAR(128), product_name TEXT,
              reason TEXT, marketplace_status VARCHAR(128), internal_status VARCHAR(64) DEFAULT 'new',
              assigned_to VARCHAR(128), operator_comment TEXT, amount VARCHAR(64), quantity INTEGER,
              created_at_marketplace TIMESTAMP, updated_at_marketplace TIMESTAMP, raw TEXT,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ]
        for stmt in ddl:
            self.db.execute(text(stmt))
        for stmt in [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_buyer_chats_platform_external ON buyer_chats(platform, external_chat_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_buyer_chat_messages_platform_external ON buyer_chat_messages(platform, external_message_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_buyer_returns_platform_external ON buyer_returns(platform, external_return_id)",
            "CREATE INDEX IF NOT EXISTS ix_raw_events_platform_block ON marketplace_raw_events(platform, block)",
        ]:
            try:
                self.db.execute(text(stmt))
            except Exception:
                pass
        self.db.commit()

    def _raw(self, platform: str, block: str, status: str, raw: Any, error: str | None = None, external_id: str | None = None) -> None:
        self.db.execute(text("""
            INSERT INTO marketplace_raw_events(platform, block, external_id, status, error, raw, created_at, updated_at)
            VALUES (:platform, :block, :external_id, :status, :error, :raw, :created_at, :updated_at)
        """), {"platform": platform, "block": block, "external_id": external_id, "status": status, "error": error, "raw": _json_dumps(raw), "created_at": _now(), "updated_at": _now()})

    async def _request(self, platform: str, block: str, method: str, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> tuple[bool, Any, str | None]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(method, url, headers=headers, params=params, json=json_body)
            try:
                data = resp.json()
            except Exception:
                data = {"text": resp.text}
            if resp.status_code >= 400:
                err = f"HTTP {resp.status_code}: {str(data)[:700]}"
                self._raw(platform, block, "failed", {"url": url, "params": params, "body": json_body, "response": data}, err)
                return False, data, err
            self._raw(platform, block, "success", {"url": url, "params": params, "body": json_body, "response": data})
            return True, data, None
        except Exception as exc:
            err = str(exc)
            self._raw(platform, block, "failed", {"url": url, "params": params, "body": json_body}, err)
            return False, None, err

    def _upsert_chat(self, platform: str, item: dict[str, Any]) -> tuple[str, str]:
        external_id = str(_get(item, "id", "chat_id", "chatId", "chatID", "conversation_id", "dialog_id", default="") or _short_hash(item))
        exists = self.db.execute(text("SELECT id FROM buyer_chats WHERE platform=:p AND external_chat_id=:e"), {"p": platform, "e": external_id}).first()
        payload = {
            "platform": platform, "external_chat_id": external_id,
            "chat_type": str(_get(item, "chat_type", "type", default="buyer") or "buyer"),
            "marketplace_status": str(_get(item, "status", "state", default="") or ""),
            "unread_count": int(_get(item, "unread_count", "unreadCount", "unread", default=0) or 0),
            "needs_response": bool(_get(item, "needs_response", "needAnswer", "unanswered", default=False) or int(_get(item, "unread_count", "unreadCount", default=0) or 0) > 0),
            "buyer_name": str(_get(item, "buyer_name", "customer_name", "client_name", "user_name", default="") or ""),
            "sku": str(_get(item, "sku", "offer_id", "offerId", "nmId", "nm_id", "product_id", default="") or ""),
            "product_name": str(_get(item, "product_name", "productName", "name", "subject", default="") or ""),
            "last_message_at": _parse_dt(_get(item, "last_message_at", "lastMessageAt", "updated_at", "updatedAt", "created_at", "createdAt")),
            "raw": _json_dumps(item), "updated_at": _now()
        }
        if exists:
            self.db.execute(text("""
                UPDATE buyer_chats SET chat_type=:chat_type, marketplace_status=:marketplace_status, unread_count=:unread_count,
                needs_response=:needs_response, buyer_name=:buyer_name, sku=:sku, product_name=:product_name,
                last_message_at=COALESCE(:last_message_at,last_message_at), raw=:raw, updated_at=:updated_at
                WHERE platform=:platform AND external_chat_id=:external_chat_id
            """), payload)
            return external_id, "updated"
        self.db.execute(text("""
            INSERT INTO buyer_chats(platform, external_chat_id, chat_type, marketplace_status, internal_status, unread_count, needs_response,
              buyer_name, sku, product_name, last_message_at, raw, created_at, updated_at)
            VALUES (:platform, :external_chat_id, :chat_type, :marketplace_status, 'new', :unread_count, :needs_response,
              :buyer_name, :sku, :product_name, :last_message_at, :raw, :created_at, :updated_at)
        """), {**payload, "created_at": _now()})
        return external_id, "created"

    def _upsert_message(self, platform: str, chat_id: str, item: dict[str, Any]) -> str:
        external_id = str(_get(item, "id", "message_id", "messageId", "event_id", "eventId", default="") or f"{chat_id}:{_short_hash(item)}")
        exists = self.db.execute(text("SELECT id FROM buyer_chat_messages WHERE platform=:p AND external_message_id=:e"), {"p": platform, "e": external_id}).first()
        payload = {"platform": platform, "external_chat_id": chat_id, "external_message_id": external_id, "direction": _direction(item), "author_name": str(_get(item, "author_name", "author", "sender_name", "senderName", "name", default="") or ""), "text": str(_get(item, "text", "message", "body", "content", default="") or ""), "message_type": str(_get(item, "message_type", "type", "event_type", default="message") or "message"), "sent_at": _parse_dt(_get(item, "sent_at", "created_at", "createdAt", "date", "timestamp", "time")), "raw": _json_dumps(item), "updated_at": _now()}
        if exists:
            self.db.execute(text("""UPDATE buyer_chat_messages SET direction=:direction, author_name=:author_name, text=:text, message_type=:message_type, sent_at=COALESCE(:sent_at,sent_at), raw=:raw, updated_at=:updated_at WHERE platform=:platform AND external_message_id=:external_message_id"""), payload)
            return "updated"
        self.db.execute(text("""INSERT INTO buyer_chat_messages(platform, external_chat_id, external_message_id, direction, author_name, text, message_type, sent_at, raw, created_at, updated_at) VALUES (:platform, :external_chat_id, :external_message_id, :direction, :author_name, :text, :message_type, :sent_at, :raw, :created_at, :updated_at)"""), {**payload, "created_at": _now()})
        return "created"

    def _recompute_chat_sla(self, platform: str, chat_id: str) -> None:
        rows = self.db.execute(text("""SELECT direction, sent_at FROM buyer_chat_messages WHERE platform=:p AND external_chat_id=:c AND sent_at IS NOT NULL ORDER BY sent_at ASC, id ASC"""), {"p": platform, "c": chat_id}).mappings().all()
        first_customer = None; first_seller = None; last_message = None
        for r in rows:
            sent = r["sent_at"]; last_message = sent
            if r["direction"] == "customer" and first_customer is None: first_customer = sent
            if first_customer and r["direction"] == "seller" and first_seller is None and sent >= first_customer: first_seller = sent
        minutes = None; sla = "unanswered"; needs = True
        if first_customer and first_seller:
            minutes = max(0, int((first_seller - first_customer).total_seconds() // 60)); sla = "in_sla" if minutes <= 10 else "late"; needs = False
        elif not first_customer:
            sla = "no_customer_message"; needs = False
        self.db.execute(text("""UPDATE buyer_chats SET first_customer_message_at=:fc, first_seller_response_at=:fs, last_message_at=COALESCE(:lm,last_message_at), response_minutes=:m, response_sla_status=:sla, needs_response=:needs, updated_at=:u WHERE platform=:p AND external_chat_id=:c"""), {"p": platform, "c": chat_id, "fc": first_customer, "fs": first_seller, "lm": last_message, "m": minutes, "sla": sla, "needs": needs, "u": _now()})

    def _upsert_return(self, platform: str, item: dict[str, Any], operation_type: str = "return_request") -> str:
        external_id = str(_get(item, "id", "return_id", "returnId", "claim_id", "claimId", "posting_number", "postingNumber", "srid", "rid", default="") or _short_hash(item))
        exists = self.db.execute(text("SELECT id FROM buyer_returns WHERE platform=:p AND external_return_id=:e"), {"p": platform, "e": external_id}).first()
        payload = {"platform": platform, "external_return_id": external_id, "order_id": str(_get(item, "order_id", "orderId", "order_uid", "srid", "rid", default="") or ""), "posting_number": str(_get(item, "posting_number", "postingNumber", "posting", default="") or ""), "sku": str(_get(item, "sku", "offer_id", "offerId", "nmId", "nm_id", "product_id", default="") or ""), "product_name": str(_get(item, "product_name", "productName", "name", "subject", default="") or ""), "reason": str(_get(item, "reason", "return_reason", "returnReason", "comment", "description", default="") or ""), "marketplace_status": str(_get(item, "status", "state", "return_status", "returnStatus", default="") or ""), "amount": str(_get(item, "amount", "price", "total", "refund_amount", "refundAmount", default="") or ""), "quantity": int(_get(item, "quantity", "qty", "count", default=1) or 1), "created_at_marketplace": _parse_dt(_get(item, "created_at", "createdAt", "date", "return_date", "returnDate")), "updated_at_marketplace": _parse_dt(_get(item, "updated_at", "updatedAt", "changed_at", "changedAt")), "raw": _json_dumps(item), "updated_at": _now()}
        if exists:
            self.db.execute(text("""UPDATE buyer_returns SET order_id=:order_id, posting_number=:posting_number, sku=:sku, product_name=:product_name, reason=:reason, marketplace_status=:marketplace_status, amount=:amount, quantity=:quantity, created_at_marketplace=COALESCE(:created_at_marketplace,created_at_marketplace), updated_at_marketplace=COALESCE(:updated_at_marketplace,updated_at_marketplace), raw=:raw, updated_at=:updated_at WHERE platform=:platform AND external_return_id=:external_return_id"""), payload); status = "updated"
        else:
            self.db.execute(text("""INSERT INTO buyer_returns(platform, external_return_id, order_id, posting_number, sku, product_name, reason, marketplace_status, internal_status, amount, quantity, created_at_marketplace, updated_at_marketplace, raw, created_at, updated_at) VALUES (:platform, :external_return_id, :order_id, :posting_number, :sku, :product_name, :reason, :marketplace_status, 'new', :amount, :quantity, :created_at_marketplace, :updated_at_marketplace, :raw, :created_at, :updated_at)"""), {**payload, "created_at": _now()}); status = "created"
        self._mirror_return_to_operation(platform, payload, operation_type)
        return status

    def _mirror_return_to_operation(self, platform: str, payload: dict[str, Any], operation_type: str) -> None:
        external_id = payload["external_return_id"]
        exists = self.db.execute(text("SELECT id FROM marketplace_operations WHERE platform=:p AND operation_type=:t AND external_id=:e"), {"p": platform, "t": operation_type, "e": external_id}).first()
        data = {"platform": platform, "operation_type": operation_type, "external_id": external_id, "document_number": payload.get("posting_number") or payload.get("order_id") or external_id, "sku": payload.get("sku") or None, "product_name": payload.get("product_name") or None, "amount": payload.get("amount") or None, "quantity": payload.get("quantity") or None, "reason": payload.get("reason") or None, "status": "synced", "marketplace_status": payload.get("marketplace_status") or None, "cx_workflow_status": "new_to_review", "raw": payload.get("raw"), "occurred_at": payload.get("created_at_marketplace") or _now(), "updated_at": _now()}
        if exists:
            self.db.execute(text("""UPDATE marketplace_operations SET document_number=:document_number, sku=:sku, product_name=:product_name, amount=:amount, quantity=:quantity, reason=:reason, status=:status, marketplace_status=:marketplace_status, raw=:raw, occurred_at=COALESCE(:occurred_at,occurred_at), updated_at=:updated_at WHERE id=:id"""), {**data, "id": exists[0]})
        else:
            self.db.execute(text("""INSERT INTO marketplace_operations(platform, operation_type, external_id, document_number, sku, product_name, amount, quantity, reason, status, marketplace_status, cx_workflow_status, raw, occurred_at, created_at, updated_at) VALUES (:platform, :operation_type, :external_id, :document_number, :sku, :product_name, :amount, :quantity, :reason, :status, :marketplace_status, :cx_workflow_status, :raw, :occurred_at, :created_at, :updated_at)"""), {**data, "created_at": _now()})

    async def sync_wb_chats(self) -> dict[str, Any]:
        token = getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")
        res = {"platform": "WB", "block": "chats", "received": 0, "created": 0, "updated": 0, "messages_created": 0, "messages_updated": 0, "errors": []}
        if not token:
            res["errors"].append("WB_API_KEY/WB_API_TOKEN не заполнен"); return res
        headers = {"Authorization": token}
        items = []
        for params in [{"limit": self.max_chats}, {"take": self.max_chats}]:
            ok, data, err = await self._request("WB", "chats_list", "GET", "https://buyer-chat-api.wildberries.ru/api/v1/seller/chats", headers=headers, params=params)
            if ok:
                items = _extract_items(data, ["chats"])
                if items: break
            elif err: res["errors"].append(err)
        res["received"] = len(items)
        for item in items[: self.max_chats]:
            chat_id, st = self._upsert_chat("WB", item); res["created" if st == "created" else "updated"] += 1
            for suffix in ["events", "messages"]:
                ok, data, err = await self._request("WB", "chat_events", "GET", f"https://buyer-chat-api.wildberries.ru/api/v1/seller/chats/{chat_id}/{suffix}", headers=headers, params={"limit": self.max_messages})
                if not ok: continue
                for msg in _extract_items(data, ["events", "messages"])[: self.max_messages]:
                    mst = self._upsert_message("WB", chat_id, msg); res["messages_created" if mst == "created" else "messages_updated"] += 1
                self._recompute_chat_sla("WB", chat_id); break
        return res

    async def sync_ozon_chats(self) -> dict[str, Any]:
        cid = getattr(settings, "ozon_client_id", ""); key = getattr(settings, "ozon_api_key", "")
        res = {"platform": "OZON", "block": "chats", "received": 0, "created": 0, "updated": 0, "messages_created": 0, "messages_updated": 0, "errors": []}
        if not cid or not key:
            res["errors"].append("OZON_CLIENT_ID/OZON_API_KEY не заполнены"); return res
        headers = {"Client-Id": cid, "Api-Key": key, "Content-Type": "application/json"}
        items = []
        for url in ["https://api-seller.ozon.ru/v2/chat/list", "https://api-seller.ozon.ru/v3/chat/list"]:
            ok, data, err = await self._request("OZON", "chats_list", "POST", url, headers=headers, json_body={"filter": {}, "limit": self.max_chats, "offset": 0})
            if ok:
                items = _extract_items(data, ["chats"])
                if items: break
            elif err: res["errors"].append(err)
        res["received"] = len(items)
        for item in items[: self.max_chats]:
            chat_id, st = self._upsert_chat("OZON", item); res["created" if st == "created" else "updated"] += 1
            ok, data, err = await self._request("OZON", "chat_history", "POST", "https://api-seller.ozon.ru/v3/chat/history", headers=headers, json_body={"chat_id": chat_id, "limit": self.max_messages})
            if not ok:
                res["errors"].append(err or "Ozon chat history failed"); continue
            for msg in _extract_items(data, ["messages"])[: self.max_messages]:
                mst = self._upsert_message("OZON", chat_id, msg); res["messages_created" if mst == "created" else "messages_updated"] += 1
            self._recompute_chat_sla("OZON", chat_id)
        return res

    async def sync_wb_returns(self) -> dict[str, Any]:
        token = getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")
        res = {"platform": "WB", "block": "returns", "received": 0, "created": 0, "updated": 0, "errors": []}
        if not token:
            res["errors"].append("WB_API_KEY/WB_API_TOKEN не заполнен"); return res
        headers = {"Authorization": token}
        items = []
        candidates = [
            ("https://returns-api.wildberries.ru/api/v1/claims", {"limit": self.max_returns}),
            ("https://returns-api.wildberries.ru/api/v1/seller/returns", {"limit": self.max_returns}),
            ("https://statistics-api.wildberries.ru/api/v1/supplier/returns", {"dateFrom": (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()}),
        ]
        for url, params in candidates:
            ok, data, err = await self._request("WB", "returns_list", "GET", url, headers=headers, params=params)
            if ok:
                items = _extract_items(data, ["returns", "claims"])
                if items: break
            elif err: res["errors"].append(err)
        res["received"] = len(items)
        for item in items[: self.max_returns]:
            st = self._upsert_return("WB", item, "return_request"); res["created" if st == "created" else "updated"] += 1
        return res

    async def sync_ozon_returns(self) -> dict[str, Any]:
        cid = getattr(settings, "ozon_client_id", ""); key = getattr(settings, "ozon_api_key", "")
        res = {"platform": "OZON", "block": "returns", "received": 0, "created": 0, "updated": 0, "errors": []}
        if not cid or not key:
            res["errors"].append("OZON_CLIENT_ID/OZON_API_KEY не заполнены"); return res
        headers = {"Client-Id": cid, "Api-Key": key, "Content-Type": "application/json"}
        now = datetime.now(timezone.utc); since = (now - timedelta(days=30)).isoformat(); to = now.isoformat()
        items = []
        for url, body in [
            ("https://api-seller.ozon.ru/v3/returns/company/fbs", {"filter": {"logistic_return_date": {"from": since, "to": to}}, "limit": self.max_returns, "last_id": ""}),
            ("https://api-seller.ozon.ru/v3/returns/company/fbo", {"filter": {"logistic_return_date": {"from": since, "to": to}}, "limit": self.max_returns, "last_id": ""}),
            ("https://api-seller.ozon.ru/v2/returns/company/fbs", {"filter": {}, "limit": self.max_returns, "last_id": ""}),
            ("https://api-seller.ozon.ru/v2/returns/company/fbo", {"filter": {}, "limit": self.max_returns, "last_id": ""}),
        ]:
            ok, data, err = await self._request("OZON", "returns_list", "POST", url, headers=headers, json_body=body)
            if ok: items.extend(_extract_items(data, ["returns"]))
            elif err: res["errors"].append(err)
        uniq = {}
        for item in items:
            uniq[str(_get(item, "id", "return_id", "posting_number", "postingNumber", default="") or _short_hash(item))] = item
        res["received"] = len(uniq)
        for item in list(uniq.values())[: self.max_returns]:
            st = self._upsert_return("OZON", item, "return_request"); res["created" if st == "created" else "updated"] += 1
        return res

    async def sync(self, platform: str = "ALL", mode: str = "hot") -> dict[str, Any]:
        self.ensure_schema()
        platform = (platform or "ALL").upper(); tasks = []
        if platform in {"ALL", "WB"}:
            tasks.append(self.sync_wb_chats())
            if mode in {"full", "returns", "operations", "nightly"}: tasks.append(self.sync_wb_returns())
        if platform in {"ALL", "OZON"}:
            tasks.append(self.sync_ozon_chats())
            if mode in {"full", "returns", "operations", "nightly"}: tasks.append(self.sync_ozon_returns())
        parts = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for p in parts:
            results.append({"ok": False, "error": str(p)} if isinstance(p, Exception) else {**p, "ok": not bool(p.get("errors"))})
        self.db.commit()
        return {"ok": any(r.get("ok") for r in results), "platform": platform, "mode": mode, "received": sum(int(r.get("received", 0) or 0) for r in results), "created": sum(int(r.get("created", 0) or 0) for r in results), "updated": sum(int(r.get("updated", 0) or 0) for r in results), "results": results, "message": "Customer Ops sync завершен. Смотри diagnostics/errors, если данных нет."}

    async def send_chat_message(self, platform: str, external_chat_id: str, message: str) -> dict[str, Any]:
        self.ensure_schema(); platform = platform.upper(); message = (message or "").strip()
        if not message: return {"ok": False, "error": "Пустое сообщение"}
        local = {"id": f"cxhub:{external_chat_id}:{_short_hash({'m': message, 'at': _now().isoformat()})}", "direction": "seller", "text": message, "created_at": _now().isoformat(), "source": "cx_hub"}
        self._upsert_message(platform, external_chat_id, local); self._recompute_chat_sla(platform, external_chat_id)
        result = {"ok": False, "status": "saved_local", "message": "Ответ сохранен в CX Hub. Отправка в МП зависит от прав API-токена."}
        if platform == "OZON" and getattr(settings, "ozon_client_id", "") and getattr(settings, "ozon_api_key", ""):
            ok, data, err = await self._request("OZON", "chat_send", "POST", "https://api-seller.ozon.ru/v1/chat/send/message", headers={"Client-Id": settings.ozon_client_id, "Api-Key": settings.ozon_api_key, "Content-Type": "application/json"}, json_body={"chat_id": external_chat_id, "text": message})
            result = {"ok": ok, "status": "sent" if ok else "saved_local_send_failed", "result": data, "error": err}
        if platform == "WB" and (getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")):
            token = getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")
            ok, data, err = await self._request("WB", "chat_send", "POST", f"https://buyer-chat-api.wildberries.ru/api/v1/seller/chats/{external_chat_id}/message", headers={"Authorization": token, "Content-Type": "application/json"}, json_body={"message": message, "text": message})
            result = {"ok": ok, "status": "sent" if ok else "saved_local_send_failed", "result": data, "error": err}
        self.db.commit(); return result

    def chat_sla_report(self, platform: str = "ALL", days: int = 30) -> dict[str, Any]:
        self.ensure_schema(); platform = (platform or "ALL").upper(); since = _now() - timedelta(days=max(1, min(days, 365)))
        params = {"since": since, "platform": platform}; where = "WHERE COALESCE(first_customer_message_at, created_at) >= :since AND (:platform='ALL' OR platform=:platform)"
        rows = self.db.execute(text(f"SELECT platform, response_minutes, needs_response, first_customer_message_at, last_message_at FROM buyer_chats {where}"), params).mappings().all()
        measured = sorted([int(r["response_minutes"]) for r in rows if r["response_minutes"] is not None])
        avg = round(sum(measured) / len(measured), 2) if measured else None
        median = measured[len(measured)//2] if measured else None
        overdue = [m for m in measured if m > 10]
        by_mp = {}
        by_day = {}
        for r in rows:
            p = r["platform"]; by_mp.setdefault(p, {"total":0,"answered":0,"unanswered":0,"overdue":0}); by_mp[p]["total"] += 1
            if r["response_minutes"] is None and r["needs_response"]: by_mp[p]["unanswered"] += 1
            if r["response_minutes"] is not None:
                by_mp[p]["answered"] += 1
                if int(r["response_minutes"]) > 10: by_mp[p]["overdue"] += 1
            base = r["first_customer_message_at"] or r["last_message_at"]; key = str(base.date()) if base else "unknown"
            by_day.setdefault(key, {"total":0,"answered":0,"overdue":0}); by_day[key]["total"] += 1
            if r["response_minutes"] is not None: by_day[key]["answered"] += 1
            if r["response_minutes"] is not None and int(r["response_minutes"]) > 10: by_day[key]["overdue"] += 1
        return {"ok": True, "platform": platform, "days": days, "sla_minutes": 10, "total_chats": len(rows), "answered_chats_count": len(measured), "unanswered_chats_count": sum(1 for r in rows if r["response_minutes"] is None and r["needs_response"]), "overdue_chats_count": len(overdue), "avg_first_response_minutes": avg, "median_first_response_minutes": median, "chat_response_rate": round((len(measured)/len(rows))*100,2) if rows else 0, "by_marketplace": by_mp, "by_day": dict(sorted(by_day.items(), reverse=True)[:30])}
