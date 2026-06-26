from __future__ import annotations
from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.customer_ops_service import CustomerOpsService, _json_loads

router = APIRouter(prefix="/customer-ops", tags=["customer-ops"])

class WorkUpdate(BaseModel):
    internal_status: str | None = None
    assigned_to: str | None = None
    operator_comment: str | None = None

class ReplyPayload(BaseModel):
    message: str

def _rows(db: Session, sql: str, params: dict[str, Any]):
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]

def _one(db: Session, sql: str, params: dict[str, Any]):
    row = db.execute(text(sql), params).mappings().first()
    return dict(row) if row else None

def _decode(row: dict[str, Any]):
    if not row: return row
    if "raw" in row: row["raw"] = _json_loads(row.get("raw"))
    for k, v in list(row.items()):
        if hasattr(v, "isoformat"): row[k] = v.isoformat()
    if "needs_response" in row: row["needs_response"] = bool(row["needs_response"])
    return row

@router.get("/summary")
def summary(platform: str = "ALL", db: Session = Depends(get_db)):
    svc = CustomerOpsService(db); svc.ensure_schema(); platform = (platform or "ALL").upper(); params = {"platform": platform}
    where = "WHERE (:platform='ALL' OR platform=:platform)"
    chats_total = db.execute(text(f"SELECT COUNT(*) FROM buyer_chats {where}"), params).scalar() or 0
    chats_unanswered = db.execute(text(f"SELECT COUNT(*) FROM buyer_chats {where} AND needs_response = TRUE"), params).scalar() or 0
    returns_total = db.execute(text(f"SELECT COUNT(*) FROM buyer_returns {where}"), params).scalar() or 0
    returns_active = db.execute(text(f"SELECT COUNT(*) FROM buyer_returns {where} AND internal_status IN ('new','in_progress','waiting_marketplace','waiting_warehouse')"), params).scalar() or 0
    errors = _rows(db, "SELECT platform, block, status, error, created_at FROM marketplace_raw_events WHERE status='failed' AND (:platform='ALL' OR platform=:platform) ORDER BY created_at DESC LIMIT 20", params)
    return {"ok": True, "platform": platform, "chats_total": chats_total, "chats_unanswered": chats_unanswered, "returns_total": returns_total, "returns_active": returns_active, "raw_errors": [_decode(x) for x in errors], "chat_sla": svc.chat_sla_report(platform=platform, days=30)}

@router.get("/chats")
def list_chats(platform: str = "ALL", status: str = "all", needs_response: bool | None = None, limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema(); filters = ["(:platform='ALL' OR platform=:platform)"]; params: dict[str, Any] = {"platform": (platform or "ALL").upper(), "limit": min(max(limit,1),500), "offset": max(offset,0)}
    if status and status != "all": filters.append("internal_status=:status"); params["status"] = status
    if needs_response is not None: filters.append("needs_response=:needs_response"); params["needs_response"] = needs_response
    items = _rows(db, f"SELECT * FROM buyer_chats WHERE {' AND '.join(filters)} ORDER BY COALESCE(last_message_at, updated_at, created_at) DESC, id DESC LIMIT :limit OFFSET :offset", params)
    return {"items": [_decode(x) for x in items]}

@router.get("/chats/{chat_id}/messages")
def chat_messages(chat_id: int, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema(); chat = _one(db, "SELECT * FROM buyer_chats WHERE id=:id", {"id": chat_id})
    if not chat: raise HTTPException(404, "Чат не найден")
    items = _rows(db, "SELECT * FROM buyer_chat_messages WHERE platform=:p AND external_chat_id=:c ORDER BY COALESCE(sent_at, created_at) ASC, id ASC", {"p": chat["platform"], "c": chat["external_chat_id"]})
    return {"chat": _decode(chat), "items": [_decode(x) for x in items]}

@router.patch("/chats/{chat_id}")
def update_chat(chat_id: int, payload: WorkUpdate, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema(); row = _one(db, "SELECT id FROM buyer_chats WHERE id=:id", {"id": chat_id})
    if not row: raise HTTPException(404, "Чат не найден")
    data = {k:v for k,v in payload.model_dump(exclude_unset=True).items() if k in {"internal_status","assigned_to","operator_comment"}}
    if data:
        sets = ", ".join([f"{k}=:{k}" for k in data]); data.update({"id": chat_id, "updated_at": datetime.utcnow()})
        db.execute(text(f"UPDATE buyer_chats SET {sets}, updated_at=:updated_at WHERE id=:id"), data); db.commit()
    return _decode(_one(db, "SELECT * FROM buyer_chats WHERE id=:id", {"id": chat_id}) or {})

@router.post("/chats/{chat_id}/reply")
async def reply_chat(chat_id: int, payload: ReplyPayload, db: Session = Depends(get_db)):
    svc = CustomerOpsService(db); svc.ensure_schema(); chat = _one(db, "SELECT * FROM buyer_chats WHERE id=:id", {"id": chat_id})
    if not chat: raise HTTPException(404, "Чат не найден")
    return await svc.send_chat_message(chat["platform"], chat["external_chat_id"], payload.message)

@router.get("/returns")
def list_returns(platform: str = "ALL", status: str = "all", limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema(); filters = ["(:platform='ALL' OR platform=:platform)"]; params: dict[str, Any] = {"platform": (platform or "ALL").upper(), "limit": min(max(limit,1),500), "offset": max(offset,0)}
    if status and status != "all": filters.append("internal_status=:status"); params["status"] = status
    items = _rows(db, f"SELECT * FROM buyer_returns WHERE {' AND '.join(filters)} ORDER BY COALESCE(created_at_marketplace, updated_at, created_at) DESC, id DESC LIMIT :limit OFFSET :offset", params)
    return {"items": [_decode(x) for x in items]}

@router.patch("/returns/{return_id}")
def update_return(return_id: int, payload: WorkUpdate, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema(); row = _one(db, "SELECT id FROM buyer_returns WHERE id=:id", {"id": return_id})
    if not row: raise HTTPException(404, "Возврат не найден")
    data = {k:v for k,v in payload.model_dump(exclude_unset=True).items() if k in {"internal_status","assigned_to","operator_comment"}}
    if data:
        sets = ", ".join([f"{k}=:{k}" for k in data]); data.update({"id": return_id, "updated_at": datetime.utcnow()})
        db.execute(text(f"UPDATE buyer_returns SET {sets}, updated_at=:updated_at WHERE id=:id"), data); db.commit()
    return _decode(_one(db, "SELECT * FROM buyer_returns WHERE id=:id", {"id": return_id}) or {})

@router.post("/sync")
async def sync_customer_ops(platform: str = "ALL", mode: str = "full", db: Session = Depends(get_db)):
    return await CustomerOpsService(db).sync(platform=platform, mode=mode)
