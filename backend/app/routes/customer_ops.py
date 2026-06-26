from __future__ import annotations
import asyncio, traceback
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import SessionLocal, get_db
from app.services.customer_ops_service import CustomerOpsService, _jl

router = APIRouter(prefix="/customer-ops", tags=["customer-ops"])
_sync_task = None
_sync_state: dict[str, Any] = {"running":False,"run_id":None,"platform":None,"mode":None,"started_at":None,"finished_at":None,"last_success_at":None,"last_error":None,"result":None}

class WorkUpdate(BaseModel):
    internal_status: str | None = None
    assigned_to: str | None = None
    operator_comment: str | None = None

class ReplyPayload(BaseModel):
    message: str

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _rows(db: Session, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in db.execute(text(sql), params).mappings().all()]

def _one(db: Session, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = db.execute(text(sql), params).mappings().first()
    return dict(row) if row else None

def _decode(row: dict[str, Any]) -> dict[str, Any]:
    if "raw" in row: row["raw"] = _jl(row.get("raw"))
    for key, value in list(row.items()):
        if hasattr(value, "isoformat"): row[key] = value.isoformat()
    if "needs_response" in row: row["needs_response"] = bool(row["needs_response"])
    return row

def _where(platform: str) -> tuple[str, dict[str, Any]]:
    p = (platform or "ALL").upper()
    return "(:platform='ALL' OR platform=:platform)", {"platform":p}

async def _run_sync_background(platform: str, mode: str, run_id: str):
    db = SessionLocal()
    try:
        result = await CustomerOpsService(db).sync(platform=platform, mode=mode)
        _sync_state.update({"running":False,"finished_at":_now_iso(),"last_success_at":_now_iso(),"last_error":None,"result":result})
    except Exception as exc:
        _sync_state.update({"running":False,"finished_at":_now_iso(),"last_error":str(exc),"trace":traceback.format_exc()[-2000:]})
    finally:
        try: db.close()
        except Exception: pass

def _start_sync(platform: str, mode: str) -> dict[str, Any]:
    global _sync_task
    if _sync_task is not None and not _sync_task.done():
        return {"started":False,"already_running":True,"status":_sync_state}
    run_id = str(uuid4())
    _sync_state.update({"running":True,"run_id":run_id,"platform":platform,"mode":mode,"started_at":_now_iso(),"finished_at":None,"last_error":None,"result":None})
    _sync_task = asyncio.create_task(_run_sync_background(platform, mode, run_id))
    return {"started":True,"already_running":False,"status":_sync_state}

@router.get("/summary")
def summary(platform: str = "ALL", db: Session = Depends(get_db)):
    svc = CustomerOpsService(db); svc.ensure_schema(); where, params = _where(platform)
    chat_total = db.execute(text(f"SELECT COUNT(*) FROM buyer_chats WHERE {where}"), params).scalar() or 0
    chat_unanswered = db.execute(text(f"SELECT COUNT(*) FROM buyer_chats WHERE {where} AND needs_response = TRUE"), params).scalar() or 0
    returns_total = db.execute(text(f"SELECT COUNT(*) FROM buyer_returns WHERE {where}"), params).scalar() or 0
    returns_active = db.execute(text(f"SELECT COUNT(*) FROM buyer_returns WHERE {where} AND internal_status IN ('new','in_progress','waiting_marketplace','waiting_warehouse')"), params).scalar() or 0
    raw_errors = _rows(db, "SELECT platform, block, status, error, created_at FROM marketplace_raw_events WHERE (:platform='ALL' OR platform=:platform) AND status IN ('failed','empty','partial') ORDER BY created_at DESC LIMIT 60", {"platform":(platform or "ALL").upper()})
    return {"ok":True,"platform":(platform or "ALL").upper(),"chats_total":chat_total,"chats_unanswered":chat_unanswered,"returns_total":returns_total,"returns_active":returns_active,"raw_errors":[_decode(x) for x in raw_errors],"chat_sla":svc.chat_sla_report(platform=platform, days=30),"sync_status":_sync_state}

@router.get("/chats")
def list_chats(platform: str = "ALL", status: str = "all", needs_response: bool | None = None, limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema(); where, params = _where(platform); filters = [where]
    if status and status != "all": filters.append("internal_status=:status"); params["status"] = status
    if needs_response is not None: filters.append("needs_response=:needs_response"); params["needs_response"] = needs_response
    params.update({"limit":min(max(limit,1),500),"offset":max(offset,0)})
    items = _rows(db, f"SELECT * FROM buyer_chats WHERE {' AND '.join(filters)} ORDER BY COALESCE(last_message_at, updated_at, created_at) DESC, id DESC LIMIT :limit OFFSET :offset", params)
    return {"platform":(platform or "ALL").upper(),"items":[_decode(x) for x in items]}

@router.get("/chats/{chat_id}/messages")
def chat_messages(chat_id: int, limit: int = 500, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema()
    chat = _one(db, "SELECT * FROM buyer_chats WHERE id=:id", {"id":chat_id})
    if not chat: raise HTTPException(404, "Чат не найден")
    items = _rows(db, "SELECT * FROM buyer_chat_messages WHERE platform=:p AND external_chat_id=:c ORDER BY COALESCE(sent_at, created_at) ASC, id ASC LIMIT :limit", {"p":chat["platform"],"c":chat["external_chat_id"],"limit":min(max(limit,1),1000)})
    return {"chat":_decode(chat),"items":[_decode(x) for x in items]}

@router.patch("/chats/{chat_id}")
def update_chat(chat_id: int, payload: WorkUpdate, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema()
    if not _one(db, "SELECT id FROM buyer_chats WHERE id=:id", {"id":chat_id}): raise HTTPException(404, "Чат не найден")
    data = payload.model_dump(exclude_unset=True); allowed = {k:v for k,v in data.items() if k in {"internal_status","assigned_to","operator_comment"}}
    if allowed:
        sets = ", ".join([f"{k}=:{k}" for k in allowed]); allowed.update({"id":chat_id,"updated_at":datetime.utcnow()})
        db.execute(text(f"UPDATE buyer_chats SET {sets}, updated_at=:updated_at WHERE id=:id"), allowed); db.commit()
    return _decode(_one(db, "SELECT * FROM buyer_chats WHERE id=:id", {"id":chat_id}) or {})

@router.post("/chats/{chat_id}/reply")
async def reply_chat(chat_id: int, payload: ReplyPayload, db: Session = Depends(get_db)):
    svc = CustomerOpsService(db); svc.ensure_schema(); chat = _one(db, "SELECT * FROM buyer_chats WHERE id=:id", {"id":chat_id})
    if not chat: raise HTTPException(404, "Чат не найден")
    return await svc.send_chat_message(chat["platform"], chat["external_chat_id"], payload.message)

@router.get("/returns")
def list_returns(platform: str = "ALL", status: str = "all", limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema(); where, params = _where(platform); filters = [where]
    if status and status != "all": filters.append("internal_status=:status"); params["status"] = status
    params.update({"limit":min(max(limit,1),500),"offset":max(offset,0)})
    items = _rows(db, f"SELECT * FROM buyer_returns WHERE {' AND '.join(filters)} ORDER BY COALESCE(created_at_marketplace, updated_at, created_at) DESC, id DESC LIMIT :limit OFFSET :offset", params)
    return {"platform":(platform or "ALL").upper(),"items":[_decode(x) for x in items]}

@router.patch("/returns/{return_id}")
def update_return(return_id: int, payload: WorkUpdate, db: Session = Depends(get_db)):
    CustomerOpsService(db).ensure_schema()
    if not _one(db, "SELECT id FROM buyer_returns WHERE id=:id", {"id":return_id}): raise HTTPException(404, "Возврат не найден")
    data = payload.model_dump(exclude_unset=True); allowed = {k:v for k,v in data.items() if k in {"internal_status","assigned_to","operator_comment"}}
    if allowed:
        sets = ", ".join([f"{k}=:{k}" for k in allowed]); allowed.update({"id":return_id,"updated_at":datetime.utcnow()})
        db.execute(text(f"UPDATE buyer_returns SET {sets}, updated_at=:updated_at WHERE id=:id"), allowed); db.commit()
    return _decode(_one(db, "SELECT * FROM buyer_returns WHERE id=:id", {"id":return_id}) or {})

@router.post("/sync")
async def sync_customer_ops(platform: str = "ALL", mode: str = "full", background: bool = False, db: Session = Depends(get_db)):
    if background: return _start_sync((platform or "ALL").upper(), mode or "full")
    return await CustomerOpsService(db).sync(platform=platform, mode=mode)

@router.post("/sync/start")
async def sync_customer_ops_start(platform: str = "ALL", mode: str = "full"):
    return _start_sync((platform or "ALL").upper(), mode or "full")

@router.get("/sync/status")
def sync_customer_ops_status():
    return _sync_state
