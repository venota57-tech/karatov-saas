from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.customer_ops_service import CustomerOpsService
router = APIRouter(prefix="/reports", tags=["reports"])
@router.get("/chat-sla")
def chat_sla(platform: str = "ALL", days: int = 30, db: Session = Depends(get_db)):
    return CustomerOpsService(db).chat_sla_report(platform=platform, days=days)
