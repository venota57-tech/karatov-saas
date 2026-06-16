from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
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


def _upsert_review(db: Session, data: dict[str, Any]) -> str:
    existing = db.query(Review).filter(Review.platform == data['platform'], Review.external_id == data['external_id']).first()
    if settings.ai_auto_classify_on_sync:
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
        setattr(existing, key, data.get(key))
    if preserved_response_origin:
        existing.response_origin = preserved_response_origin
    if data.get('final_answer'):
        existing.final_answer = data.get('final_answer')
    if settings.ai_auto_classify_on_sync and not existing.ai_category:
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


async def sync_ozon_block(db: Session, block: str) -> dict[str, Any]:
    if not settings.ozon_sync_enabled:
        raise RuntimeError('OZON_SYNC_ENABLED=false. Включи Ozon в .env и перезапусти приложение.')
    oz = _client()
    limit = max(1, int(settings.ozon_sync_take))
    result = {'platform': 'OZON', 'block': block, 'created': 0, 'updated': 0, 'received': 0, 'diagnostics': {}}
    if block == 'reviews_unanswered':
        items, diag = await oz.get_reviews_unanswered(limit)
        result['diagnostics'] = diag
        result['received'] = len(items)
        for item in items:
            data = normalize_ozon_review(item, source_status='ozon_unanswered', operational_status='needs_response', has_answer=False)
            r = _upsert_review(db, data)
            result['created' if r == 'created' else 'updated'] += 1
    elif block == 'reviews_answered':
        items, diag = await oz.get_reviews_answered(limit)
        result['diagnostics'] = diag
        result['received'] = len(items)
        for item in items:
            data = normalize_ozon_review(item, source_status='ozon_answered', operational_status='analytics_only', has_answer=True)
            r = _upsert_review(db, data)
            result['created' if r == 'created' else 'updated'] += 1
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
            r = _upsert_question(db, data)
            result['created' if r == 'created' else 'updated'] += 1
    else:
        raise ValueError(f'Неизвестный блок Ozon: {block}')
    result['message'] = f'Ozon блок {block} завершен: получено {result["received"]}'
    return result


async def sync_ozon_all(db: Session) -> dict[str, Any]:
    _ozon_status['last_started_at'] = _now_iso()
    _ozon_status['last_error'] = None
    blocks = ['reviews_unanswered','questions_unanswered','reviews_answered','questions_answered']
    results = []
    for block in blocks:
        try:
            res = await sync_ozon_block(db, block)
            _ozon_status['blocks'][block] = {'status': 'success', 'last_result': res, 'last_success_at': _now_iso()}
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
    return dict(_ozon_status)


# v3.1: optional Ozon automatic drip sync. It runs one block per interval so Ozon and WB are visually/operationally separate.
import asyncio

OZON_SYNC_BLOCKS = ['reviews_unanswered', 'questions_unanswered', 'reviews_answered', 'questions_answered']
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
                        _ozon_status['blocks'][block] = {'status': 'success', 'last_result': res, 'last_success_at': _now_iso(), 'last_finished_at': _now_iso()}
                        _ozon_status['last_result'] = {'platform': 'OZON', 'auto': True, 'result': res}
                        _ozon_status['last_success_at'] = _now_iso()
                        _ozon_status['last_error'] = None
                    finally:
                        db.close()
        except Exception as exc:
            _ozon_status['last_error'] = f'auto_ozon: {exc}'
        await asyncio.sleep(max(60, int(settings.ozon_auto_sync_interval_seconds)))
