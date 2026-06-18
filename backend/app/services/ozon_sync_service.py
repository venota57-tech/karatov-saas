from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from app.services.marketplace_truth_service import apply_marketplace_answer
from ..models import Review, Question
from ..database import SessionLocal
from ..ai.rule_based import classify_review, classify_question
from ..marketplace_clients.ozon import OzonClient, normalize_ozon_review, normalize_ozon_question

_ozon_status: dict[str, Any] = {
    'enabled': settings.ozon_sync_enabled,
    'last_started_at': None,
    'last_finished_at': None,
    'last_success_at': None,
    'last_error': None,
    'last_result': None,
    'blocks': {},
    'cursors': {},
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _client() -> OzonClient:
    return OzonClient(
        settings.ozon_client_id,
        settings.ozon_api_key,
        request_timeout_seconds=settings.ozon_request_timeout_seconds,
        request_pause_seconds=settings.ozon_request_pause_seconds,
    )


def _is_ozon_no_text_review(data: dict[str, Any]) -> bool:
    return data.get('platform') == 'OZON' and data.get('operational_status') == 'no_text_rating'


def _upsert_review(db: Session, data: dict[str, Any]) -> str:
    existing = db.query(Review).filter(Review.platform == data['platform'], Review.external_id == data['external_id']).first()
    if settings.ai_auto_classify_on_sync and not _is_ozon_no_text_review(data):
        local = classify_review(data.get('text'), data.get('rating'), data.get('pros'), data.get('cons'))
        data.setdefault('ai_category', local.get('category'))
        data.setdefault('ai_sentiment', local.get('sentiment'))
        data.setdefault('ai_risk_level', local.get('risk_level'))
        data.setdefault('ai_tags', local.get('tags', []))
        data.setdefault('ai_reason', local.get('reason'))
    if not existing:
        db.add(Review(**data))
        db.commit()
        return 'created'
    preserved_response_origin = existing.response_origin if existing.response_origin in {'auto_app', 'manual_app'} else None
    for key in ['sku','product_name','rating','text','pros','cons','client_name','created_at_marketplace','has_answer','raw','source_status','operational_status','last_seen_source','last_seen_at','publish_blocked_reason','response_origin','ai_tags']:
        if key in data:
            setattr(existing, key, data.get(key))
    if preserved_response_origin and not _is_ozon_no_text_review(data):
        existing.response_origin = preserved_response_origin
    if data.get('final_answer'):
        existing.final_answer = data.get('final_answer')
    if _is_ozon_no_text_review(data):
        existing.ai_category = data.get('ai_category')
        existing.ai_sentiment = data.get('ai_sentiment')
        existing.ai_risk_level = data.get('ai_risk_level')
        existing.ai_reason = data.get('ai_reason')
        existing.status = 'no_text_rating'
        existing.draft_answer = None
        existing.final_answer = data.get('final_answer')
    elif settings.ai_auto_classify_on_sync and not existing.ai_category:
        existing.ai_category = data.get('ai_category')
        existing.ai_sentiment = data.get('ai_sentiment')
        existing.ai_risk_level = data.get('ai_risk_level')
        existing.ai_reason = data.get('ai_reason')
    db.commit()
    return 'updated'


def _upsert_question(db: Session, data: dict[str, Any]) -> str:
    existing = db.query(Question).filter(Question.platform == data['platform'], Question.external_id == data['external_id']).first()
    if settings.ai_auto_classify_on_sync:
        local = classify_question(data.get('text'))
        data.setdefault('ai_category', local.get('category'))
        data.setdefault('ai_risk_level', local.get('risk_level'))
        data.setdefault('ai_tags', local.get('tags', []))
        data.setdefault('ai_reason', local.get('reason'))
    if not existing:
        db.add(Question(**data))
        db.commit()
        return 'created'
    preserved_response_origin = existing.response_origin if existing.response_origin in {'auto_app', 'manual_app'} else None
    for key in ['sku','product_name','text','client_name','created_at_marketplace','has_answer','raw','source_status','operational_status','last_seen_source','last_seen_at','publish_blocked_reason','response_origin','ai_tags']:
        setattr(existing, key, data.get(key))
    if preserved_response_origin:
        existing.response_origin = preserved_response_origin
    if data.get('final_answer'):
        existing.final_answer = data.get('final_answer')
    if settings.ai_auto_classify_on_sync and not existing.ai_category:
        existing.ai_category = data.get('ai_category')
        existing.ai_risk_level = data.get('ai_risk_level')
        existing.ai_reason = data.get('ai_reason')
    db.commit()
    return 'updated'


async def _sync_ozon_reviews_paginated(db: Session, block: str, *, answered: bool) -> dict[str, Any]:
    oz = _client()
    limit = max(1, int(settings.ozon_sync_take))
    pages = max(1, int(getattr(settings, 'ozon_sync_pages_per_block_run', 100)))
    cursor_key = f'{block}:last_id'
    last_id = _ozon_status.setdefault('cursors', {}).get(cursor_key)
    result = {'platform': 'OZON', 'block': block, 'created': 0, 'updated': 0, 'received': 0, 'no_text_reviews': 0, 'pages': 0, 'cursor_key': cursor_key, 'start_last_id': last_id, 'diagnostics': {'pages': []}}
    for _ in range(pages):
        if answered:
            items, diag = await oz.get_reviews_answered_page(limit, last_id)
            source_status = 'ozon_answered'
            operational_status = 'analytics_only'
            has_answer = True
        else:
            items, diag = await oz.get_reviews_unanswered_page(limit, last_id)
            source_status = 'ozon_unanswered'
            operational_status = 'needs_response'
            has_answer = False
        result['pages'] += 1
        result['received'] += len(items)
        result['diagnostics']['pages'].append(diag)
        for item in items:
            data = normalize_ozon_review(item, source_status=source_status, operational_status=operational_status, has_answer=has_answer)
            data = apply_marketplace_answer(data, item, force_answered=bool(has_answer))
            if data.get('operational_status') == 'no_text_rating':
                result['no_text_reviews'] += 1
            r = _upsert_review(db, data)
            result['created' if r == 'created' else 'updated'] += 1
        last_id = diag.get('last_id')
        if last_id:
            _ozon_status['cursors'][cursor_key] = last_id
        if not diag.get('has_next') or not items:
            result['diagnostics']['end_reached'] = True
            result['diagnostics']['last_saved_cursor'] = _ozon_status['cursors'].get(cursor_key)
            break
    result['finish_last_id'] = _ozon_status['cursors'].get(cursor_key)
    result['message'] = f'Ozon блок {block}: получено {result["received"]}, новых {result["created"]}, обновлено {result["updated"]}, без текста {result["no_text_reviews"]}'
    return result


async def sync_ozon_block(db: Session, block: str) -> dict[str, Any]:
    if not settings.ozon_sync_enabled:
        raise RuntimeError('OZON_SYNC_ENABLED=false. Включи Ozon в .env и перезапусти приложение.')
    oz = _client()
    limit = max(1, int(settings.ozon_sync_take))
    result = {'platform': 'OZON', 'block': block, 'created': 0, 'updated': 0, 'received': 0, 'diagnostics': {}}
    if block == 'reviews_unanswered':
        return await _sync_ozon_reviews_paginated(db, block, answered=False)
    elif block == 'reviews_answered':
        return await _sync_ozon_reviews_paginated(db, block, answered=True)
    elif block == 'questions_unanswered':
        items, diag = await oz.get_questions_unanswered(limit)
        result['diagnostics'] = diag
        result['received'] = len(items)
        for item in items:
            data = normalize_ozon_question(item, source_status='ozon_unanswered', operational_status='needs_response', has_answer=False)
            r = _upsert_question(db, data)
            result['created' if r == 'created' else 'updated'] += 1
    elif block == 'questions_answered':
        items, diag = await oz.get_questions_answered(limit)
        result['diagnostics'] = diag
        result['received'] = len(items)
        for item in items:
            data = normalize_ozon_question(item, source_status='ozon_answered', operational_status='analytics_only', has_answer=True)
            data = apply_marketplace_answer(data, item, force_answered=True)
            r = _upsert_question(db, data)
            result['created' if r == 'created' else 'updated'] += 1
    else:
        raise ValueError(f'Неизвестный блок Ozon: {block}')
    result['message'] = f'Ozon блок {block} завершен: получено {result["received"]}'
    return result


async def sync_ozon_all(db: Session) -> dict[str, Any]:
    _ozon_status['last_started_at'] = _now_iso()
    _ozon_status['last_error'] = None
    blocks = ['reviews_unanswered','reviews_answered','questions_unanswered','questions_answered']
    results = []
    for block in blocks:
        try:
            res = await sync_ozon_block(db, block)
            _ozon_status['blocks'][block] = {'status': 'success', 'last_result': res, 'last_success_at': _now_iso(), 'last_finished_at': _now_iso()}
            results.append(res)
        except Exception as exc:
            err = str(exc)
            _ozon_status['blocks'][block] = {'status': 'failed', 'last_error': err, 'last_finished_at': _now_iso()}
            results.append({'platform':'OZON','block':block,'status':'failed','error':err})
    _ozon_status['last_finished_at'] = _now_iso()
    _ozon_status['last_success_at'] = _now_iso()
    _ozon_status['last_result'] = {'platform': 'OZON', 'results': results}
    return _ozon_status['last_result']


def get_ozon_status() -> dict[str, Any]:
    _ozon_status['enabled'] = settings.ozon_sync_enabled
    _ozon_status['has_client_id'] = bool(settings.ozon_client_id)
    _ozon_status['has_api_key'] = bool(settings.ozon_api_key)
    _ozon_status['sync_take'] = settings.ozon_sync_take
    _ozon_status['pages_per_block_run'] = getattr(settings, 'ozon_sync_pages_per_block_run', 5)
    return dict(_ozon_status)


import asyncio

OZON_SYNC_BLOCKS = ['reviews_unanswered', 'reviews_answered', 'questions_unanswered', 'questions_answered']
_ozon_auto_index = 0
_ozon_lock = asyncio.Lock()

async def sync_ozon_next_block(db: Session) -> dict[str, Any]:
    global _ozon_auto_index
    block = OZON_SYNC_BLOCKS[_ozon_auto_index % len(OZON_SYNC_BLOCKS)]
    _ozon_auto_index += 1
    return await sync_ozon_block(db, block)

async def ozon_auto_sync_loop() -> None:
    if not settings.ozon_sync_enabled or not settings.ozon_auto_sync_enabled:
        return
    await asyncio.sleep(max(0, int(settings.ozon_auto_sync_initial_delay_seconds)))
    while True:
        try:
            if not _ozon_lock.locked():
                async with _ozon_lock:
                    db = SessionLocal()
                    try:
                        _ozon_status['last_started_at'] = _now_iso()
                        res = await sync_ozon_next_block(db)
                        block = res.get('block')
                        finished_at = _now_iso()
                        _ozon_status['blocks'][block] = {'status': 'success', 'last_result': res, 'last_success_at': finished_at, 'last_finished_at': finished_at}
                        _ozon_status['last_result'] = {'platform': 'OZON', 'auto': True, 'result': res}
                        _ozon_status['last_success_at'] = finished_at
                        _ozon_status['last_error'] = None
                    finally:
                        _ozon_status['last_finished_at'] = _now_iso()
                        db.close()
        except Exception as exc:
            _ozon_status['last_error'] = f'auto_ozon: {exc}'
            _ozon_status['last_finished_at'] = _now_iso()
        await asyncio.sleep(max(60, int(settings.ozon_auto_sync_interval_seconds)))
