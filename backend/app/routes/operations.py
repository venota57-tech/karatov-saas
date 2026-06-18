from __future__ import annotations
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
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
        'status': getattr(row, 'workflow_status', None) or row.status,
        'source_status': getattr(row, 'source_status', None),
        'workflow_status': getattr(row, 'workflow_status', None) or row.status,
        'responsible': row.responsible,
        'comment': row.comment,
        'occurred_at': row.occurred_at.isoformat() if row.occurred_at else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        'raw': row.raw,
    }

@router.get('')
def list_operations(
    platform: str = 'ALL',
    operation_type: str = 'all',
    status: str = 'all',
    offset: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    q = db.query(MarketplaceOperation)
    if platform and platform.upper() != 'ALL':
        q = q.filter(MarketplaceOperation.platform == platform.upper())
    if operation_type and operation_type != 'all':
        q = q.filter(MarketplaceOperation.operation_type == operation_type)
    if status and status != 'all':
        q = q.filter(
            (MarketplaceOperation.status == status) |
            (getattr(MarketplaceOperation, 'workflow_status', MarketplaceOperation.status) == status)
        )

    total = q.count()
    safe_limit = min(max(int(limit or 500), 1), 1000)
    safe_offset = max(int(offset or 0), 0)

    rows = (
        q.order_by(MarketplaceOperation.occurred_at.desc().nullslast(), MarketplaceOperation.id.desc())
        .offset(safe_offset)
        .limit(safe_limit)
        .all()
    )

    return {
        'total': total,
        'offset': safe_offset,
        'limit': safe_limit,
        'items': [_serialize(r) for r in rows],
    }

@router.get('/summary')
def summary(platform: str = 'ALL', db: Session = Depends(get_db)):
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
    from ..services.operations_sync_service import OperationsSyncService
    return await OperationsSyncService(db).sync(platform=platform)
