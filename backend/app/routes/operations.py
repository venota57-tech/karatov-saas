from __future__ import annotations
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db, run_lightweight_migrations
from ..models import MarketplaceOperation

router = APIRouter(prefix='/operations', tags=['operations'])

OPERATION_TYPES = ['return', 'act', 'shortage', 'surplus', 'anonymization', 'discrepancy']

class OperationUpdate(BaseModel):
    status: str | None = None
    responsible: str | None = None
    comment: str | None = None


def _serialize(row: MarketplaceOperation) -> dict[str, Any]:
    return {
        'id': row.id,
        'platform': row.platform,
        'operation_type': row.operation_type,
        'external_id': row.external_id,
        'document_number': row.document_number,
        'sku': row.sku,
        'product_name': row.product_name,
        'warehouse': row.warehouse,
        'amount': row.amount,
        'quantity': row.quantity,
        'reason': row.reason,
        'status': row.status,
        'responsible': row.responsible,
        'comment': row.comment,
        'occurred_at': row.occurred_at.isoformat() if row.occurred_at else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        'raw': row.raw,
    }

@router.get('')
def list_operations(platform: str = 'ALL', operation_type: str = 'all', status: str = 'all', limit: int = 100, offset: int = 0, db: Session = Depends(get_db)):
    run_lightweight_migrations()
    q = db.query(MarketplaceOperation)
    if platform and platform.upper() != 'ALL':
        q = q.filter(MarketplaceOperation.platform == platform.upper())
    if operation_type and operation_type != 'all':
        q = q.filter(MarketplaceOperation.operation_type == operation_type)
    if status and status != 'all':
        q = q.filter(MarketplaceOperation.status == status)
    safe_limit = min(max(int(limit or 100), 1), 500)
    safe_offset = max(int(offset or 0), 0)
    rows = q.order_by(MarketplaceOperation.occurred_at.desc().nullslast(), MarketplaceOperation.id.desc()).offset(safe_offset).limit(safe_limit).all()
    return {'items': [_serialize(r) for r in rows]}

@router.get('/summary')
def summary(platform: str = 'ALL', db: Session = Depends(get_db)):
    run_lightweight_migrations()
    q = db.query(MarketplaceOperation)
    if platform and platform.upper() != 'ALL':
        q = q.filter(MarketplaceOperation.platform == platform.upper())
    total = q.count()
    by_type = dict(q.with_entities(MarketplaceOperation.operation_type, func.count(MarketplaceOperation.id)).group_by(MarketplaceOperation.operation_type).all())
    by_status = dict(q.with_entities(MarketplaceOperation.status, func.count(MarketplaceOperation.id)).group_by(MarketplaceOperation.status).all())
    return {
        'total': total,
        'by_type': by_type,
        'by_status': by_status,
        'api_status': {
            'wb': 'live_adapter_enabled',
            'ozon': 'live_adapter_enabled',
            'message': 'Live adapter подключен: операции тянутся из доступных API WB/Ozon. Если метод недоступен по правам токена, это отображается в результате синхронизации без демо-данных.',
        }
    }

@router.patch('/{operation_id}')
def update_operation(operation_id: int, payload: OperationUpdate, db: Session = Depends(get_db)):
    row = db.get(MarketplaceOperation, operation_id)
    if not row:
        raise HTTPException(404, 'Операция не найдена')
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _serialize(row)

@router.post('/sync')
async def sync_operations(platform: str = 'ALL', db: Session = Depends(get_db)):
    run_lightweight_migrations()
    from ..services.operations_sync_service import OperationsSyncService
    return await OperationsSyncService(db).sync(platform=platform)


# ---- RC1.6.2: non-blocking operations sync status ----

import asyncio as _ops_asyncio
import traceback as _ops_traceback
from datetime import datetime as _ops_datetime, timezone as _ops_timezone
from uuid import uuid4 as _ops_uuid4

_operations_sync_state = {
    "running": False,
    "run_id": None,
    "platform": None,
    "started_at": None,
    "finished_at": None,
    "elapsed_seconds": 0,
    "last_success_at": None,
    "last_error": None,
    "result": None,
}
_operations_sync_task = None


def _ops_now():
    return _ops_datetime.now(_ops_timezone.utc)


def _ops_iso(dt):
    return dt.isoformat() if dt else None


def _ops_public_state():
    started = _operations_sync_state.get("started_at")
    if _operations_sync_state.get("running") and started:
        try:
            _operations_sync_state["elapsed_seconds"] = int((_ops_now() - _ops_datetime.fromisoformat(started)).total_seconds())
        except Exception:
            pass
    return dict(_operations_sync_state)


async def _run_operations_sync_background(platform: str, run_id: str):
    from app.database import SessionLocal
    from app.services.operations_sync_service import OperationsSyncService

    db = SessionLocal()
    try:
        result = await OperationsSyncService(db).sync(platform=platform)
        _operations_sync_state.update({
            "running": False,
            "finished_at": _ops_iso(_ops_now()),
            "last_success_at": _ops_iso(_ops_now()),
            "last_error": None,
            "result": result,
        })
    except Exception as exc:
        _operations_sync_state.update({
            "running": False,
            "finished_at": _ops_iso(_ops_now()),
            "last_error": str(exc),
            "trace": _ops_traceback.format_exc()[-2000:],
        })
    finally:
        try:
            db.close()
        except Exception:
            pass


def _start_operations_sync(platform: str = "ALL"):
    global _operations_sync_task

    if _operations_sync_task is not None and not _operations_sync_task.done():
        return {
            "started": False,
            "already_running": True,
            "status": _ops_public_state(),
        }

    run_id = str(_ops_uuid4())
    now = _ops_iso(_ops_now())
    _operations_sync_state.update({
        "running": True,
        "run_id": run_id,
        "platform": platform,
        "started_at": now,
        "finished_at": None,
        "elapsed_seconds": 0,
        "last_error": None,
        "result": None,
    })

    _operations_sync_task = _ops_asyncio.create_task(_run_operations_sync_background(platform, run_id))
    return {
        "started": True,
        "already_running": False,
        "status": _ops_public_state(),
    }


@router.get("/sync/status")
def operations_sync_status():
    return _ops_public_state()


@router.post("/sync/start")
async def operations_sync_start(platform: str = "ALL"):
    return _start_operations_sync(platform)
