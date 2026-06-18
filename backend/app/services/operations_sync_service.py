from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import MarketplaceOperation


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except Exception:
        return None


def _walk(obj: Any, keys: set[str]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if obj.get(key) not in (None, ''):
                return obj.get(key)
        for value in obj.values():
            found = _walk(value, keys)
            if found not in (None, ''):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _walk(value, keys)
            if found not in (None, ''):
                return found
    return None


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    result = data.get('result', data)
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    if isinstance(result, dict):
        for key in ['items', 'returns', 'operations', 'postings', 'acts', 'documents', 'data', 'list']:
            value = result.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    for key in ['items', 'returns', 'operations', 'postings', 'acts', 'documents', 'data', 'list']:
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _first(obj: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if obj.get(key) not in (None, ''):
            return obj.get(key)
    return None


def _upsert_operation(db: Session, data: dict[str, Any]) -> str:
    existing = (
        db.query(MarketplaceOperation)
        .filter(
            MarketplaceOperation.platform == data['platform'],
            MarketplaceOperation.operation_type == data['operation_type'],
            MarketplaceOperation.external_id == data['external_id'],
        )
        .first()
    )
    if not existing:
        db.add(MarketplaceOperation(**data))
        db.commit()
        return 'created'
    for key, value in data.items():
        if key == 'external_id':
            continue
        setattr(existing, key, value)
    existing.updated_at = _now_utc()
    db.commit()
    return 'updated'


class OperationsSyncService:
    """Live marketplace operations adapter.

    The adapter never creates demo rows. If a marketplace endpoint is unavailable for the token,
    it returns diagnostics/errors and leaves the registry unchanged.
    """

    def __init__(self, db: Session):
        self.db = db
        self.timeout = httpx.Timeout(30.0, connect=10.0)

    async def sync(self, platform: str = 'ALL', days: int = 31) -> dict[str, Any]:
        platform = (platform or 'ALL').upper()
        results = []
        if platform in {'ALL', 'WB'}:
            results.append(await self.sync_wb(days=days))
        if platform in {'ALL', 'OZON'}:
            results.append(await self.sync_ozon(days=days))
        created = sum(int(r.get('created', 0)) for r in results)
        updated = sum(int(r.get('updated', 0)) for r in results)
        received = sum(int(r.get('received', 0)) for r in results)
        return {
            'ok': True,
            'platform': platform,
            'received': received,
            'created': created,
            'updated': updated,
            'results': results,
            'message': f'Operations sync завершен: получено {received}, создано {created}, обновлено {updated}.',
        }

    async def sync_wb(self, days: int = 31) -> dict[str, Any]:
        token = getattr(settings, 'wb_api_token', '') or getattr(settings, 'wb_api_key', '')
        result = {'platform': 'WB', 'received': 0, 'created': 0, 'updated': 0, 'blocks': []}
        if not token:
            result['error'] = 'WB_API_KEY/WB_API_TOKEN не заполнен'
            return result
        headers = {'Authorization': token}
        date_to = datetime.now(timezone.utc).date()
        date_from = date_to - timedelta(days=min(max(days, 1), 31))
        # Official WB reports include goods returns. Other operation documents remain token/API-permission dependent.
        attempts = [
            {
                'kind': 'return',
                'url': 'https://seller-analytics-api.wildberries.ru/api/v1/analytics/goods-return',
                'params': {'dateFrom': date_from.isoformat(), 'dateTo': date_to.isoformat()},
            },
        ]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in attempts:
                block = {'operation_type': attempt['kind'], 'endpoint': attempt['url'], 'created': 0, 'updated': 0, 'received': 0}
                try:
                    resp = await client.get(attempt['url'], headers=headers, params=attempt.get('params'))
                    if resp.status_code >= 400:
                        block['error'] = f'HTTP {resp.status_code}: {resp.text[:800]}'
                        result['blocks'].append(block)
                        continue
                    data = resp.json()
                    items = _extract_items(data)
                    block['received'] = len(items)
                    for item in items:
                        status = self._upsert_wb_return(item)
                        block['created' if status == 'created' else 'updated'] += 1
                    result['blocks'].append(block)
                except Exception as exc:
                    block['error'] = str(exc)[:1000]
                    result['blocks'].append(block)
        result['received'] = sum(b.get('received', 0) for b in result['blocks'])
        result['created'] = sum(b.get('created', 0) for b in result['blocks'])
        result['updated'] = sum(b.get('updated', 0) for b in result['blocks'])
        return result

    def _upsert_wb_return(self, item: dict[str, Any]) -> str:
        ext = _first(item, ['id', 'returnId', 'return_id', 'srid', 'orderId', 'order_id', 'barcode']) or _walk(item, {'id','returnId','return_id','srid','orderId','order_id','barcode'})
        sku = _first(item, ['nmId', 'nmID', 'nm_id', 'sku', 'supplierArticle', 'article']) or _walk(item, {'nmId','nmID','nm_id','sku','supplierArticle','article'})
        data = {
            'platform': 'WB',
            'operation_type': 'return',
            'external_id': str(ext or f"wb-return-{hash(str(item))}"),
            'document_number': str(_first(item, ['docNumber','document_number','returnId','srid']) or ext or ''),
            'sku': str(sku) if sku not in (None, '') else None,
            'product_name': _first(item, ['subject','product_name','name','goodsName']),
            'warehouse': _first(item, ['warehouseName','warehouse','officeName']),
            'amount': str(_first(item, ['price','retailPrice','amount','sum']) or '') or None,
            'quantity': int(_first(item, ['quantity','qty']) or 1),
            'reason': _first(item, ['reason','returnReason','comment','status']),
            'status': 'new',
            'source_status': str(_first(item, ['status','state','returnStatus','docStatus']) or '') or None,
            'workflow_status': 'new',
            'raw': item,
            'occurred_at': _parse_dt(_first(item, ['date','returnDate','createdAt','lastChangeDate'])) or _now_utc(),
        }
        return _upsert_operation(self.db, data)

    async def sync_ozon(self, days: int = 31) -> dict[str, Any]:
        if not settings.ozon_client_id or not settings.ozon_api_key:
            return {'platform': 'OZON', 'received': 0, 'created': 0, 'updated': 0, 'error': 'OZON_CLIENT_ID/OZON_API_KEY не заполнены'}
        headers = {'Client-Id': settings.ozon_client_id, 'Api-Key': settings.ozon_api_key, 'Content-Type': 'application/json'}
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=max(1, days))
        # Ozon FBS acts are an official seller API family. Returns endpoint availability depends on seller role/API version.
        attempts = [
            {
                'kind': 'act',
                'path': '/v2/posting/fbs/act/list',
                'payload': {'date_from': date_from.isoformat(), 'date_to': date_to.isoformat(), 'limit': 100},
            },
            {
                'kind': 'return',
                'path': '/v1/returns/list',
                'payload': {'filter': {'created_since': date_from.isoformat(), 'created_to': date_to.isoformat()}, 'limit': 100},
            },
        ]
        result = {'platform': 'OZON', 'received': 0, 'created': 0, 'updated': 0, 'blocks': []}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in attempts:
                block = {'operation_type': attempt['kind'], 'endpoint': attempt['path'], 'created': 0, 'updated': 0, 'received': 0}
                try:
                    resp = await client.post(f"https://api-seller.ozon.ru{attempt['path']}", headers=headers, json=attempt['payload'])
                    if resp.status_code >= 400:
                        block['error'] = f'HTTP {resp.status_code}: {resp.text[:800]}'
                        result['blocks'].append(block)
                        continue
                    data = resp.json()
                    items = _extract_items(data)
                    block['received'] = len(items)
                    for item in items:
                        status = self._upsert_ozon_operation(attempt['kind'], item)
                        block['created' if status == 'created' else 'updated'] += 1
                    result['blocks'].append(block)
                except Exception as exc:
                    block['error'] = str(exc)[:1000]
                    result['blocks'].append(block)
                await asyncio.sleep(1.0)
        result['received'] = sum(b.get('received', 0) for b in result['blocks'])
        result['created'] = sum(b.get('created', 0) for b in result['blocks'])
        result['updated'] = sum(b.get('updated', 0) for b in result['blocks'])
        return result

    def _upsert_ozon_operation(self, kind: str, item: dict[str, Any]) -> str:
        ext = _first(item, ['id','act_id','actId','posting_number','postingNumber','return_id','returnId','number']) or _walk(item, {'id','act_id','actId','posting_number','postingNumber','return_id','returnId','number'})
        sku = _first(item, ['sku','product_id','productId','offer_id','offerId']) or _walk(item, {'sku','product_id','productId','offer_id','offerId'})
        data = {
            'platform': 'OZON',
            'operation_type': 'act' if kind == 'act' else 'return',
            'external_id': str(ext or f"ozon-{kind}-{hash(str(item))}"),
            'document_number': str(_first(item, ['number','act_id','actId','posting_number','postingNumber','return_id','returnId']) or ext or ''),
            'sku': str(sku) if sku not in (None, '') else None,
            'product_name': _first(item, ['name','product_name','productName']),
            'warehouse': _first(item, ['warehouse','warehouse_name','warehouseName','delivery_method_name']),
            'amount': str(_first(item, ['amount','price','total_price','totalPrice']) or '') or None,
            'quantity': int(_first(item, ['quantity','qty']) or 1),
            'reason': _first(item, ['reason','return_reason_name','status','state']),
            'status': 'new',
            'source_status': str(_first(item, ['status','state','returnStatus','docStatus']) or '') or None,
            'workflow_status': 'new',
            'raw': item,
            'occurred_at': _parse_dt(_first(item, ['created_at','createdAt','date','act_date','actDate','return_date','returnDate'])) or _now_utc(),
        }
        return _upsert_operation(self.db, data)
