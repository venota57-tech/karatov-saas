from __future__ import annotations

import asyncio, hashlib, json, os
from datetime import datetime, timedelta, timezone
from typing import Any
import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.config import settings

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _jd(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"raw": str(value)}, ensure_ascii=False)

def _jl(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value

def _hash(value: Any) -> str:
    return hashlib.sha1(_jd(value).encode("utf-8")).hexdigest()[:24]

def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 100000000000:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return _dt(int(raw))
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None

def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)

def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj.get(k) not in (None, ""):
                return obj.get(k)
        for v in obj.values():
            found = _get(v, *keys, default=None)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _get(v, *keys, default=None)
            if found not in (None, ""):
                return found
    return default

def _items(data: Any, preferred: list[str] | None = None) -> list[dict[str, Any]]:
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
        for key in preferred + ["items","chats","events","messages","returns","claims","operations","postings","acts","documents","list","data","rows","result"]:
            value = root.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                nested = _items(value, preferred)
                if nested:
                    return nested
    return []

def _direction(item: dict[str, Any]) -> str:
    raw = " ".join(str(x or "").lower() for x in [
        _get(item, "direction", "sender", "author", "source", "user_type", "participant_type", "owner", "type"),
        _get(item, "from", "from_type", "userType", "participantType"),
    ])
    if any(x in raw for x in ["seller","merchant","operator","support","продав","оператор","сотрудник"]):
        return "seller"
    if any(x in raw for x in ["buyer","customer","client","покуп","клиент"]):
        return "customer"
    if item.get("isSeller") is True or item.get("is_seller") is True or item.get("fromSeller") is True:
        return "seller"
    return "customer"

def _msg_text(item: dict[str, Any]) -> str:
    direct = _get(item, "text", "message_text", "messageText", "body", "content", "value", "comment", "caption", "description", default=None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    if isinstance(direct, (int, float)):
        return str(direct)
    message = item.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    if isinstance(message, dict):
        nested = _msg_text(message)
        if nested:
            return nested
    for key in ["payload","data","event","message","body","content","lastMessage"]:
        v = item.get(key)
        if isinstance(v, dict):
            nested = _msg_text(v)
            if nested:
                return nested
    for node in _walk(item):
        for k, v in node.items():
            kl = str(k).lower()
            if kl.endswith("id") or kl in {"id","uuid","status","type","date","time","timestamp","url","replysign"}:
                continue
            if isinstance(v, str) and v.strip() and len(v.strip()) > 1 and not v.strip().startswith("http"):
                return v.strip()
    if _get(item, "attachments", "files", "images", default=None):
        return "[вложение]"
    return ""

def _msg_time(item: dict[str, Any], fallback: Any = None) -> datetime | None:
    for key in ["sent_at","sentAt","created_at","createdAt","addTimestamp","add_timestamp","date","timestamp","time","event_time","eventTime","messageCreatedAt","lastMessageAt"]:
        parsed = _dt(_get(item, key, default=None))
        if parsed:
            return parsed
    return _dt(fallback)

def _chat_id(item: dict[str, Any]) -> str:
    return str(_get(item, "id", "chat_id", "chatId", "chatID", "conversation_id", "dialog_id", default="") or _hash(item))

def _message_id(chat_id: str, item: dict[str, Any]) -> str:
    return str(_get(item, "id", "message_id", "messageId", "event_id", "eventId", "uuid", default="") or f"{chat_id}:{_hash(item)}")

class CustomerOpsService:
    def __init__(self, db: Session):
        self.db = db
        self.timeout = httpx.Timeout(float(os.getenv("CUSTOMER_OPS_REQUEST_TIMEOUT", "20")), connect=8.0)
        self.max_chats = int(os.getenv("CUSTOMER_OPS_MAX_CHATS", "60") or "60")
        self.max_messages = int(os.getenv("CUSTOMER_OPS_MAX_MESSAGES_PER_CHAT", "100") or "100")
        self.max_returns = int(os.getenv("CUSTOMER_OPS_MAX_RETURNS", "150") or "150")

    def ensure_schema(self) -> None:
        dialect = self.db.bind.dialect.name
        id_type = "SERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
        bool_type = "BOOLEAN DEFAULT FALSE" if dialect == "postgresql" else "BOOLEAN DEFAULT 0"
        ddl = [
            f"CREATE TABLE IF NOT EXISTS marketplace_raw_events (id {id_type}, platform VARCHAR(32) NOT NULL, block VARCHAR(128) NOT NULL, external_id VARCHAR(256), status VARCHAR(64) DEFAULT 'received', error TEXT, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            f"CREATE TABLE IF NOT EXISTS buyer_chats (id {id_type}, platform VARCHAR(32) NOT NULL, external_chat_id VARCHAR(256) NOT NULL, chat_type VARCHAR(64), marketplace_status VARCHAR(128), internal_status VARCHAR(64) DEFAULT 'new', unread_count INTEGER DEFAULT 0, needs_response {bool_type}, buyer_name VARCHAR(256), sku VARCHAR(128), product_name TEXT, assigned_to VARCHAR(128), operator_comment TEXT, reply_sign TEXT, first_customer_message_at TIMESTAMP, first_seller_response_at TIMESTAMP, last_message_at TIMESTAMP, response_minutes INTEGER, response_sla_status VARCHAR(64), raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            f"CREATE TABLE IF NOT EXISTS buyer_chat_messages (id {id_type}, platform VARCHAR(32) NOT NULL, external_chat_id VARCHAR(256) NOT NULL, external_message_id VARCHAR(256) NOT NULL, direction VARCHAR(32) DEFAULT 'customer', author_name VARCHAR(256), text TEXT, message_type VARCHAR(64), sent_at TIMESTAMP, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            f"CREATE TABLE IF NOT EXISTS buyer_returns (id {id_type}, platform VARCHAR(32) NOT NULL, external_return_id VARCHAR(256) NOT NULL, order_id VARCHAR(256), posting_number VARCHAR(256), sku VARCHAR(128), product_name TEXT, reason TEXT, marketplace_status VARCHAR(128), internal_status VARCHAR(64) DEFAULT 'new', assigned_to VARCHAR(128), operator_comment TEXT, amount VARCHAR(64), quantity INTEGER, created_at_marketplace TIMESTAMP, updated_at_marketplace TIMESTAMP, raw TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        ]
        for stmt in ddl:
            self.db.execute(text(stmt))
        for table, col, typ in [
            ("buyer_chats","reply_sign","TEXT"),("buyer_chats","assigned_to","VARCHAR(128)"),("buyer_chats","operator_comment","TEXT"),
            ("buyer_returns","assigned_to","VARCHAR(128)"),("buyer_returns","operator_comment","TEXT"),
        ]:
            try:
                self.db.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}"))
            except Exception:
                try:
                    self.db.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                except Exception:
                    pass
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
        self.db.execute(text("INSERT INTO marketplace_raw_events(platform, block, external_id, status, error, raw, created_at, updated_at) VALUES (:p,:b,:e,:s,:err,:r,:c,:u)"),
            {"p":platform,"b":block,"e":external_id,"s":status,"err":error,"r":_jd(raw),"c":_now(),"u":_now()})

    async def _request(self, platform: str, block: str, method: str, url: str, *, headers: dict[str,str], params: dict[str,Any] | None = None, json_body: dict[str,Any] | None = None, record_failure: bool = True) -> tuple[bool, Any, str | None]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(method, url, headers=headers, params=params, json=json_body)
            try:
                data = resp.json()
            except Exception:
                data = {"text": resp.text}
            if resp.status_code >= 400:
                err = f"HTTP {resp.status_code}: {str(data)[:700]}"
                if record_failure:
                    self._raw(platform, block, "failed", {"url":url,"params":params,"body":json_body,"response":data}, err)
                return False, data, err
            self._raw(platform, block, "success", {"url":url,"params":params,"body":json_body,"response":data})
            return True, data, None
        except Exception as exc:
            err = str(exc)
            if record_failure:
                self._raw(platform, block, "failed", {"url":url,"params":params,"body":json_body}, err)
            return False, None, err

    def _upsert_chat(self, platform: str, item: dict[str,Any]) -> tuple[str, str]:
        cid = _chat_id(item)
        last_msg = item.get("lastMessage") if isinstance(item.get("lastMessage"), dict) else {}
        last_at = _msg_time(item) or _msg_time(last_msg)
        reply_sign = str(_get(item, "replySign", "reply_sign", default="") or "")
        exists = self.db.execute(text("SELECT id FROM buyer_chats WHERE platform=:p AND external_chat_id=:c"), {"p":platform,"c":cid}).first()
        payload = {
            "p":platform,"c":cid,
            "chat_type":str(_get(item,"chat_type","type","category",default="buyer") or "buyer"),
            "marketplace_status":str(_get(item,"status","state","chat_status","chatStatus",default="") or ""),
            "unread_count":int(_get(item,"unread_count","unreadCount","unread",default=0) or 0),
            "needs_response":bool(_get(item,"needs_response","needAnswer","unanswered",default=False) or int(_get(item,"unread_count","unreadCount",default=0) or 0) > 0),
            "buyer_name":str(_get(item,"buyer_name","customer_name","client_name","user_name","buyerName",default="") or ""),
            "sku":str(_get(item,"sku","offer_id","offerId","nmId","nm_id","product_id","posting_number","postingNumber",default="") or ""),
            "product_name":str(_get(item,"product_name","productName","name","subject",default="") or ""),
            "reply_sign":reply_sign,"last_message_at":last_at,"raw":_jd(item),"u":_now()
        }
        if exists:
            self.db.execute(text("UPDATE buyer_chats SET chat_type=:chat_type, marketplace_status=:marketplace_status, unread_count=:unread_count, needs_response=:needs_response, buyer_name=:buyer_name, sku=:sku, product_name=:product_name, reply_sign=COALESCE(NULLIF(:reply_sign,''), reply_sign), last_message_at=COALESCE(:last_message_at,last_message_at), raw=:raw, updated_at=:u WHERE platform=:p AND external_chat_id=:c"), payload)
            return cid, "updated"
        self.db.execute(text("INSERT INTO buyer_chats(platform, external_chat_id, chat_type, marketplace_status, internal_status, unread_count, needs_response, buyer_name, sku, product_name, reply_sign, last_message_at, raw, created_at, updated_at) VALUES (:p,:c,:chat_type,:marketplace_status,'new',:unread_count,:needs_response,:buyer_name,:sku,:product_name,:reply_sign,:last_message_at,:raw,:u,:u)"), payload)
        return cid, "created"

    def _upsert_message(self, platform: str, chat_id: str, item: dict[str,Any], fallback_time: Any = None) -> str:
        mid = _message_id(chat_id, item)
        txt = _msg_text(item)
        sent = _msg_time(item, fallback_time) or _now()
        exists = self.db.execute(text("SELECT id FROM buyer_chat_messages WHERE platform=:p AND external_message_id=:m"), {"p":platform,"m":mid}).first()
        payload = {"p":platform,"c":chat_id,"m":mid,"direction":_direction(item),"author":str(_get(item,"author_name","author","sender_name","senderName","name","user_name",default="") or ""),"txt":txt,"type":str(_get(item,"message_type","type","event_type","eventType",default="message") or "message"),"sent":sent,"raw":_jd(item),"u":_now()}
        if exists:
            self.db.execute(text("UPDATE buyer_chat_messages SET direction=:direction, author_name=:author, text=COALESCE(NULLIF(:txt,''), text), message_type=:type, sent_at=COALESCE(:sent,sent_at), raw=:raw, updated_at=:u WHERE platform=:p AND external_message_id=:m"), payload)
            return "updated"
        self.db.execute(text("INSERT INTO buyer_chat_messages(platform, external_chat_id, external_message_id, direction, author_name, text, message_type, sent_at, raw, created_at, updated_at) VALUES (:p,:c,:m,:direction,:author,:txt,:type,:sent,:raw,:u,:u)"), payload)
        return "created"

    def _recompute_sla(self, platform: str, chat_id: str) -> None:
        rows = self.db.execute(text("SELECT direction, sent_at FROM buyer_chat_messages WHERE platform=:p AND external_chat_id=:c AND sent_at IS NOT NULL ORDER BY sent_at ASC, id ASC"), {"p":platform,"c":chat_id}).mappings().all()
        first_customer = first_seller = last_message = last_direction = None
        for r in rows:
            sent = r["sent_at"]; last_message = sent; last_direction = r["direction"]
            if r["direction"] == "customer" and first_customer is None:
                first_customer = sent
            if first_customer and r["direction"] == "seller" and first_seller is None and sent >= first_customer:
                first_seller = sent
        minutes = None; sla = "unanswered"; needs = True
        if first_customer and first_seller:
            minutes = max(0, int((first_seller - first_customer).total_seconds() // 60))
            sla = "in_sla" if minutes <= 10 else "late"
            needs = last_direction == "customer"
        elif not first_customer:
            sla = "no_customer_message"; needs = False
        self.db.execute(text("UPDATE buyer_chats SET first_customer_message_at=:fc, first_seller_response_at=:fs, last_message_at=COALESCE(:lm,last_message_at), response_minutes=:m, response_sla_status=:sla, needs_response=:needs, updated_at=:u WHERE platform=:p AND external_chat_id=:c"), {"p":platform,"c":chat_id,"fc":first_customer,"fs":first_seller,"lm":last_message,"m":minutes,"sla":sla,"needs":needs,"u":_now()})

    def _upsert_return(self, platform: str, item: dict[str,Any], op_type: str = "return_request") -> str:
        ext = str(_get(item,"id","return_id","returnId","claim_id","claimId","posting_number","postingNumber","srid","rid",default="") or _hash(item))
        exists = self.db.execute(text("SELECT id FROM buyer_returns WHERE platform=:p AND external_return_id=:e"), {"p":platform,"e":ext}).first()
        payload = {
            "p":platform,"e":ext,
            "order_id":str(_get(item,"order_id","orderId","order_uid","srid","rid",default="") or ""),
            "posting":str(_get(item,"posting_number","postingNumber","posting",default="") or ""),
            "sku":str(_get(item,"sku","offer_id","offerId","nmId","nm_id","product_id",default="") or ""),
            "name":str(_get(item,"product_name","productName","name","subject",default="") or ""),
            "reason":str(_get(item,"reason","return_reason","returnReason","comment","description",default="") or ""),
            "ms":str(_get(item,"status","state","return_status","returnStatus",default="") or ""),
            "amount":str(_get(item,"amount","price","total","refund_amount","refundAmount",default="") or ""),
            "qty":int(_get(item,"quantity","qty","count",default=1) or 1),
            "cm":_msg_time(item, _get(item,"return_date","returnDate","created_at","createdAt")),
            "um":_msg_time(item, _get(item,"updated_at","updatedAt","changed_at","changedAt")),
            "raw":_jd(item),"u":_now()
        }
        if exists:
            self.db.execute(text("UPDATE buyer_returns SET order_id=:order_id, posting_number=:posting, sku=:sku, product_name=:name, reason=:reason, marketplace_status=:ms, amount=:amount, quantity=:qty, created_at_marketplace=COALESCE(:cm,created_at_marketplace), updated_at_marketplace=COALESCE(:um,updated_at_marketplace), raw=:raw, updated_at=:u WHERE platform=:p AND external_return_id=:e"), payload)
            st = "updated"
        else:
            self.db.execute(text("INSERT INTO buyer_returns(platform, external_return_id, order_id, posting_number, sku, product_name, reason, marketplace_status, internal_status, amount, quantity, created_at_marketplace, updated_at_marketplace, raw, created_at, updated_at) VALUES (:p,:e,:order_id,:posting,:sku,:name,:reason,:ms,'new',:amount,:qty,:cm,:um,:raw,:u,:u)"), payload)
            st = "created"
        self._mirror_return(platform, payload, op_type)
        return st

    def _mirror_return(self, platform: str, p: dict[str,Any], op_type: str) -> None:
        exists = self.db.execute(text("SELECT id FROM marketplace_operations WHERE platform=:p AND operation_type=:t AND external_id=:e"), {"p":platform,"t":op_type,"e":p["e"]}).first()
        data = {"p":platform,"t":op_type,"e":p["e"],"doc":p.get("posting") or p.get("order_id") or p["e"],"sku":p.get("sku") or None,"name":p.get("name") or None,"amount":p.get("amount") or None,"qty":p.get("qty") or 1,"reason":p.get("reason") or None,"ms":p.get("ms") or None,"raw":p.get("raw"),"at":p.get("cm") or _now(),"u":_now()}
        if exists:
            self.db.execute(text("UPDATE marketplace_operations SET document_number=:doc, sku=:sku, product_name=:name, amount=:amount, quantity=:qty, reason=:reason, status='synced', marketplace_status=:ms, raw=:raw, occurred_at=COALESCE(:at,occurred_at), updated_at=:u WHERE id=:id"), {**data,"id":exists[0]})
        else:
            self.db.execute(text("INSERT INTO marketplace_operations(platform, operation_type, external_id, document_number, sku, product_name, amount, quantity, reason, status, marketplace_status, cx_workflow_status, raw, occurred_at, created_at, updated_at) VALUES (:p,:t,:e,:doc,:sku,:name,:amount,:qty,:reason,'synced',:ms,'new_to_review',:raw,:at,:u,:u)"), data)

    async def sync_wb_chats(self) -> dict[str,Any]:
        token = getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")
        res = {"platform":"WB","block":"chats","received":0,"created":0,"updated":0,"messages_created":0,"messages_updated":0,"errors":[]}
        if not token:
            err = "WB_API_KEY/WB_API_TOKEN не заполнен"; res["errors"].append(err); self._raw("WB","chats_list","failed",{},err); return res
        headers = {"Authorization": token}
        ok, data, err = await self._request("WB","chats_list","GET","https://buyer-chat-api.wildberries.ru/api/v1/seller/chats",headers=headers,params={"limit":self.max_chats})
        if not ok:
            res["errors"].append(err or "WB chats_list failed"); return res
        chats = _items(data, ["chats"]); res["received"] = len(chats)
        for item in chats[:self.max_chats]:
            cid, st = self._upsert_chat("WB", item); res["created" if st == "created" else "updated"] += 1
            lm = item.get("lastMessage") if isinstance(item.get("lastMessage"), dict) else None
            if lm:
                msg = {**lm, "chatID": cid}
                mst = self._upsert_message("WB", cid, msg, fallback_time=_msg_time(item)); res["messages_created" if mst == "created" else "messages_updated"] += 1
                self._recompute_sla("WB", cid)
        # WB Chat Events are a GLOBAL feed: /api/v1/seller/events?next=<timestamp>, not /chats/{id}/events.
        next_ts = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
        ok, ev_data, ev_err = await self._request("WB","chat_events","GET","https://buyer-chat-api.wildberries.ru/api/v1/seller/events",headers=headers,params={"next":next_ts,"limit":self.max_chats * 4},record_failure=True)
        if ok:
            events = _items(ev_data, ["events","messages"])
            for ev in events[:self.max_chats * 4]:
                cid = str(_get(ev,"chatID","chatId","chat_id","id",default="") or "")
                if not cid:
                    continue
                self._upsert_chat("WB", {"id":cid, "lastMessage":ev, "status":"event"})
                mst = self._upsert_message("WB", cid, ev)
                res["messages_created" if mst == "created" else "messages_updated"] += 1
                self._recompute_sla("WB", cid)
        elif ev_err:
            res["errors"].append(ev_err)
        if not chats and not res["errors"]:
            self._raw("WB","chats_list","empty",data or {},"WB chats API returned 0 chats")
        return res

    async def sync_ozon_chats(self) -> dict[str,Any]:
        cid = getattr(settings, "ozon_client_id", ""); key = getattr(settings, "ozon_api_key", "")
        res = {"platform":"OZON","block":"chats","received":0,"created":0,"updated":0,"messages_created":0,"messages_updated":0,"errors":[]}
        if not cid or not key:
            err = "OZON_CLIENT_ID/OZON_API_KEY не заполнены"; res["errors"].append(err); self._raw("OZON","chats_list","failed",{},err); return res
        headers = {"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"}
        ok, data, err = await self._request("OZON","chats_list","POST","https://api-seller.ozon.ru/v3/chat/list",headers=headers,json_body={"filter":{},"limit":self.max_chats,"offset":0},record_failure=False)
        if not ok:
            ok2, data2, err2 = await self._request("OZON","chats_list","POST","https://api-seller.ozon.ru/v2/chat/list",headers=headers,json_body={"filter":{},"limit":self.max_chats,"offset":0},record_failure=False)
            if ok2: data = data2
            else:
                final = err or err2 or "Ozon chat list failed"; res["errors"].append(final); self._raw("OZON","chats_list","failed",{"v3_error":err,"v2_error":err2},final); return res
        chats = _items(data, ["chats"]); res["received"] = len(chats)
        for item in chats[:self.max_chats]:
            chat_id, st = self._upsert_chat("OZON", item); res["created" if st == "created" else "updated"] += 1
            ok, hist, hist_err = await self._request("OZON","chat_history","POST","https://api-seller.ozon.ru/v3/chat/history",headers=headers,json_body={"chat_id":chat_id,"limit":self.max_messages})
            if not ok:
                res["errors"].append(hist_err or "Ozon chat history failed"); continue
            for msg in _items(hist, ["messages"])[:self.max_messages]:
                mst = self._upsert_message("OZON", chat_id, msg, fallback_time=_msg_time(item))
                res["messages_created" if mst == "created" else "messages_updated"] += 1
            self._recompute_sla("OZON", chat_id)
        if not chats and not res["errors"]:
            self._raw("OZON","chats_list","empty",data or {},"Ozon chat list returned 0 chats")
        return res

    async def sync_wb_returns(self) -> dict[str,Any]:
        token = getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")
        res = {"platform":"WB","block":"returns","received":0,"created":0,"updated":0,"errors":[]}
        if not token:
            err = "WB_API_KEY/WB_API_TOKEN не заполнен"; res["errors"].append(err); self._raw("WB","returns_list","failed",{},err); return res
        headers = {"Authorization": token}
        candidates = [
            ("https://returns-api.wildberries.ru/api/v1/claims", {"limit": self.max_returns}),
            ("https://returns-api.wildberries.ru/api/v1/seller/returns", {"limit": self.max_returns}),
            ("https://statistics-api.wildberries.ru/api/v1/supplier/returns", {"dateFrom": (datetime.now(timezone.utc)-timedelta(days=30)).date().isoformat()}),
        ]
        all_errors = []; items = []
        for url, params in candidates:
            ok, data, err = await self._request("WB","returns_list","GET",url,headers=headers,params=params,record_failure=False)
            if ok:
                got = _items(data, ["returns","claims"])
                if got: items = got; break
            elif err: all_errors.append(err)
        if not items:
            self._raw("WB","returns_list","failed" if all_errors else "empty",{"errors":all_errors}, "; ".join(all_errors) if all_errors else "WB returns returned 0 rows")
            res["errors"].extend(all_errors); return res
        res["received"] = len(items)
        for item in items[:self.max_returns]:
            st = self._upsert_return("WB", item); res["created" if st == "created" else "updated"] += 1
        return res

    async def sync_ozon_returns(self) -> dict[str,Any]:
        cid = getattr(settings, "ozon_client_id", ""); key = getattr(settings, "ozon_api_key", "")
        res = {"platform":"OZON","block":"returns","received":0,"created":0,"updated":0,"errors":[]}
        if not cid or not key:
            err = "OZON_CLIENT_ID/OZON_API_KEY не заполнены"; res["errors"].append(err); self._raw("OZON","returns_list","failed",{},err); return res
        headers = {"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"}
        items = []; errors = []
        for url in ["https://api-seller.ozon.ru/v3/returns/company/fbs","https://api-seller.ozon.ru/v3/returns/company/fbo"]:
            ok, data, err = await self._request("OZON","returns_list","POST",url,headers=headers,json_body={"filter":{},"limit":self.max_returns,"last_id":0},record_failure=False)
            if ok: items.extend(_items(data, ["returns"]))
            elif err: errors.append(err)
        if errors:
            self._raw("OZON","returns_list","failed" if not items else "partial",{"errors":errors}, "; ".join(errors)); res["errors"].extend(errors)
        uniq = {}
        for item in items:
            key_id = str(_get(item,"id","return_id","posting_number","postingNumber",default="") or _hash(item)); uniq[key_id] = item
        res["received"] = len(uniq)
        for item in list(uniq.values())[:self.max_returns]:
            st = self._upsert_return("OZON", item); res["created" if st == "created" else "updated"] += 1
        if not items and not errors:
            self._raw("OZON","returns_list","empty",{},"Ozon returns returned 0 rows")
        return res

    async def sync(self, platform: str = "ALL", mode: str = "hot") -> dict[str,Any]:
        self.ensure_schema(); platform = (platform or "ALL").upper()
        tasks = []
        if platform in {"ALL","WB"}:
            tasks.append(self.sync_wb_chats())
            if mode in {"full","returns","operations","nightly"}: tasks.append(self.sync_wb_returns())
        if platform in {"ALL","OZON"}:
            tasks.append(self.sync_ozon_chats())
            if mode in {"full","returns","operations","nightly"}: tasks.append(self.sync_ozon_returns())
        parts = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for p in parts:
            results.append({"ok":False,"error":str(p)} if isinstance(p, Exception) else {**p, "ok": not bool(p.get("errors"))})
        self.db.commit()
        return {"ok": any(r.get("ok") for r in results), "platform": platform, "mode": mode, "received": sum(int(r.get("received",0) or 0) for r in results), "created": sum(int(r.get("created",0) or 0) for r in results), "updated": sum(int(r.get("updated",0) or 0) for r in results), "results": results, "message": "Customer Ops sync завершен. Если данных нет, смотри diagnostics/raw_events по WB/Ozon."}

    async def send_chat_message(self, platform: str, external_chat_id: str, message: str) -> dict[str,Any]:
        self.ensure_schema(); platform = platform.upper(); message = (message or "").strip()
        if not message: return {"ok":False,"error":"Пустое сообщение"}
        self._upsert_message(platform, external_chat_id, {"id":f"cxhub:{external_chat_id}:{_hash({'message':message,'at':_now().isoformat()})}", "direction":"seller", "text":message, "created_at":_now().isoformat(), "source":"cx_hub"})
        self._recompute_sla(platform, external_chat_id)
        result = {"ok":False,"status":"saved_local","message":"Ответ сохранен локально. Отправка в маркетплейс не выполнена."}
        if platform == "OZON":
            cid = getattr(settings, "ozon_client_id", ""); key = getattr(settings, "ozon_api_key", "")
            if cid and key:
                ok, data, err = await self._request("OZON","chat_send","POST","https://api-seller.ozon.ru/v1/chat/send/message",headers={"Client-Id":cid,"Api-Key":key,"Content-Type":"application/json"},json_body={"chat_id":external_chat_id,"text":message})
                result = {"ok":ok,"status":"sent" if ok else "saved_local_send_failed","result":data,"error":err}
        elif platform == "WB":
            token = getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")
            row = self.db.execute(text("SELECT reply_sign FROM buyer_chats WHERE platform='WB' AND external_chat_id=:c"), {"c":external_chat_id}).mappings().first()
            reply_sign = row["reply_sign"] if row else None
            if token and reply_sign:
                ok, data, err = await self._request("WB","chat_send","POST","https://buyer-chat-api.wildberries.ru/api/v1/seller/message",headers={"Authorization":token,"Content-Type":"application/json"},json_body={"replySign":reply_sign,"message":message,"text":message})
                result = {"ok":ok,"status":"sent" if ok else "saved_local_send_failed","result":data,"error":err}
            elif token:
                result = {"ok":False,"status":"saved_local_no_reply_sign","message":"Ответ сохранен локально. Для отправки в WB нужен replySign из списка чатов."}
        self.db.commit(); return result

    def chat_sla_report(self, platform: str = "ALL", days: int = 30) -> dict[str,Any]:
        self.ensure_schema(); platform = (platform or "ALL").upper()
        params = {"since": _now() - timedelta(days=max(1,min(int(days or 30),365))), "platform": platform}
        rows = self.db.execute(text("SELECT platform, response_minutes, response_sla_status, needs_response, first_customer_message_at, last_message_at FROM buyer_chats WHERE COALESCE(first_customer_message_at, created_at) >= :since AND (:platform='ALL' OR platform=:platform)"), params).mappings().all()
        measured = [int(r["response_minutes"]) for r in rows if r["response_minutes"] is not None]; measured_sorted = sorted(measured)
        by_platform = {}; by_day = {}
        for r in rows:
            p = r["platform"]; by_platform.setdefault(p, {"total":0,"answered":0,"unanswered":0,"overdue":0}); by_platform[p]["total"] += 1
            if r["response_minutes"] is not None:
                by_platform[p]["answered"] += 1
                if int(r["response_minutes"]) > 10: by_platform[p]["overdue"] += 1
            elif r["needs_response"]: by_platform[p]["unanswered"] += 1
            base = r["first_customer_message_at"] or r["last_message_at"]; key = str(base.date()) if base else "unknown"
            by_day.setdefault(key, {"total":0,"answered":0,"overdue":0}); by_day[key]["total"] += 1
            if r["response_minutes"] is not None:
                by_day[key]["answered"] += 1
                if int(r["response_minutes"]) > 10: by_day[key]["overdue"] += 1
        avg = round(sum(measured) / len(measured), 2) if measured else 0
        median = measured_sorted[len(measured_sorted)//2] if measured_sorted else 0
        return {"ok":True,"platform":platform,"days":days,"sla_minutes":10,"total_chats":len(rows),"answered_chats_count":len(measured),"unanswered_chats_count":sum(1 for r in rows if r["response_minutes"] is None and r["needs_response"]),"overdue_chats_count":sum(1 for m in measured if m > 10),"avg_first_response_minutes":avg,"median_first_response_minutes":median,"chat_response_rate":round((len(measured)/len(rows))*100,2) if rows else 0,"by_marketplace":by_platform,"by_day":dict(sorted(by_day.items(), reverse=True)[:30])}
