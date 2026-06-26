from __future__ import annotations
from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from app.database import get_db, run_lightweight_migrations
from app.models import MarketplaceOperation
from app.services.customer_ops_service import CustomerOpsService
router = APIRouter(prefix="/operations", tags=["operations"])
class OperationUpdate(BaseModel):
    status: str | None = None
    cx_workflow_status: str | None = None
    responsible: str | None = None
    comment: str | None = None

def _serialize(row: MarketplaceOperation) -> dict[str, Any]:
    return {"id": row.id, "platform": row.platform, "operation_type": row.operation_type, "external_id": row.external_id, "document_number": row.document_number, "sku": row.sku, "product_name": row.product_name, "warehouse": row.warehouse, "amount": row.amount, "quantity": row.quantity, "reason": row.reason, "status": row.status, "marketplace_status": getattr(row,"marketplace_status",None), "cx_workflow_status": getattr(row,"cx_workflow_status",None), "responsible": row.responsible, "comment": row.comment, "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None, "created_at": row.created_at.isoformat() if row.created_at else None, "updated_at": row.updated_at.isoformat() if row.updated_at else None, "raw": row.raw}

def _filter(q, platform: str, operation_type: str, status: str):
    if platform and platform.upper() != "ALL": q = q.filter(MarketplaceOperation.platform == platform.upper())
    if operation_type and operation_type != "all": q = q.filter(MarketplaceOperation.operation_type == operation_type)
    if status and status != "all": q = q.filter((MarketplaceOperation.status == status) | (MarketplaceOperation.cx_workflow_status == status))
    return q

@router.get("")
def list_operations(platform: str = "ALL", operation_type: str = "all", status: str = "all", limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    run_lightweight_migrations(); CustomerOpsService(db).ensure_schema(); q = _filter(db.query(MarketplaceOperation), platform, operation_type, status)
    rows = q.order_by(MarketplaceOperation.occurred_at.desc().nullslast(), MarketplaceOperation.id.desc()).offset(max(offset,0)).limit(min(max(limit,1),500)).all()
    return {"items": [_serialize(r) for r in rows]}

@router.get("/summary")
def summary(platform: str = "ALL", db: Session = Depends(get_db)):
    run_lightweight_migrations(); svc = CustomerOpsService(db); svc.ensure_schema(); q = db.query(MarketplaceOperation)
    if platform and platform.upper() != "ALL": q = q.filter(MarketplaceOperation.platform == platform.upper())
    total = q.count(); by_type = dict(q.with_entities(MarketplaceOperation.operation_type, func.count(MarketplaceOperation.id)).group_by(MarketplaceOperation.operation_type).all()); by_status = dict(q.with_entities(MarketplaceOperation.status, func.count(MarketplaceOperation.id)).group_by(MarketplaceOperation.status).all()); by_workflow = dict(q.with_entities(MarketplaceOperation.cx_workflow_status, func.count(MarketplaceOperation.id)).group_by(MarketplaceOperation.cx_workflow_status).all())
    returns_active = db.execute(text("SELECT COUNT(*) FROM buyer_returns WHERE (:platform='ALL' OR platform=:platform) AND internal_status IN ('new','in_progress','waiting_marketplace','waiting_warehouse')"), {"platform": (platform or "ALL").upper()}).scalar() or 0
    return {"total": total, "total_operations": total, "by_type": by_type, "by_status": by_status, "by_workflow_status": by_workflow, "returns_active": returns_active, "chat_sla": svc.chat_sla_report(platform=platform, days=30), "api_status": {"wb": "live_adapter_enabled", "ozon": "live_adapter_enabled", "message": "Operations Hub v1: операции, чаты и возвраты хранятся как реальные данные; недоступные API пишутся в diagnostics/raw_events без демо-строк."}}

@router.patch("/{operation_id}")
def update_operation(operation_id: int, payload: OperationUpdate, db: Session = Depends(get_db)):
    row = db.get(MarketplaceOperation, operation_id)
    if not row: raise HTTPException(404, "Операция не найдена")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if hasattr(row, k): setattr(row, k, v)
    row.updated_at = datetime.utcnow(); db.commit(); db.refresh(row); return _serialize(row)

@router.post("/sync")
async def sync_operations(platform: str = "ALL", db: Session = Depends(get_db)):
    run_lightweight_migrations(); from app.services.operations_sync_service import OperationsSyncService
    ops = await OperationsSyncService(db).sync(platform=platform); customer = await CustomerOpsService(db).sync(platform=platform, mode="operations")
    return {"ok": bool(ops.get("ok", True) or customer.get("ok", True)), "operations": ops, "customer_ops": customer, "message": "Операции, возвраты и чаты синхронизированы доступными API."}
