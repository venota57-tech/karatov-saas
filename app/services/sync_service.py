from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import Review, Question, RatingSnapshot
from ..marketplace_clients.wb import WildberriesClient, WbRateLimitError, normalize_feedback, normalize_question
from ..ai.rule_based import classify_review, classify_question

_sync_lock = asyncio.Lock()

WB_SYNC_BLOCKS = [
    'feedbacks_unanswered',
    'questions_unanswered',
    'feedbacks_answered',
    'questions_answered',
    'feedbacks_archive',
]
WB_OPERATIONAL_BLOCKS = ['feedbacks_unanswered', 'questions_unanswered']
WB_BACKFILL_BLOCKS = ['feedbacks_answered', 'questions_answered', 'feedbacks_archive']
HISTORICAL_BLOCKS = set(WB_BACKFILL_BLOCKS)

_STATE_FILE = Path(__file__).resolve().parent / 'wb_sync_scheduler_state_v2.json'
_scheduler_index = 0

_sync_status: dict[str, Any] = {
    'auto_sync_enabled': settings.wb_auto_sync_enabled,
    'strategy': 'rc1.1_per_block_scheduler',
    'running': False,
    'last_started_at': None,
    'last_finished_at': None,
    'last_success_at': None,
    'last_error': None,
    'last_result': None,
    'progress': None,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _set_progress(step: str, **extra: Any) -> None:
    _sync_status['progress'] = {'step': step, 'at': _now_iso(), **extra}


def _base_block_state(name: str) -> dict[str, Any]:
    return {
        'status': 'never_run',
        'last_started_at': None,
        'last_finished_at': None,
        'last_success_at': None,
        'last_error': None,
        'next_retry_at': None,
        'last_result': None,
        'next_page': 0,
        'last_page': None,
        'last_skip': None,
        'last_received': None,
        'cooldown_source': None,
        'rate_limited_count': 0,
    }


def _load_state() -> dict[str, dict[str, Any]]:
    state = {name: _base_block_state(name) for name in WB_SYNC_BLOCKS}
    try:
        if _STATE_FILE.exists():
            raw = json.loads(_STATE_FILE.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                for name in WB_SYNC_BLOCKS:
                    if isinstance(raw.get(name), dict):
                        state[name].update(raw[name])
    except Exception as exc:
        _sync_status['state_load_error'] = str(exc)
    return state


_block_state: dict[str, dict[str, Any]] = _load_state()


def _save_state() -> None:
    try:
        _STATE_FILE.write_text(json.dumps(_block_state, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        _sync_status['state_save_error'] = str(exc)


def _enabled_blocks() -> list[str]:
    mode = settings.effective_wb_sync_mode()
    if mode == 'unanswered':
        # RC1.1: even if env still says unanswered, health/backfill should not be dead.
        return list(WB_SYNC_BLOCKS)
    if mode == 'answered':
        return list(WB_BACKFILL_BLOCKS)
    return list(WB_SYNC_BLOCKS)


def _cooldown_seconds_from_error(exc: Exception | str | None) -> int:
    if isinstance(exc, WbRateLimitError) and exc.retry_after_seconds:
        return max(60, min(1800, int(exc.retry_after_seconds)))
    default = getattr(settings, 'wb_rate_limit_cooldown_seconds', 120)
    return max(60, min(1800, int(default or 120)))


def _is_rate_limit_error(exc: Exception | str | None) -> bool:
    if isinstance(exc, WbRateLimitError):
        return True
    text = str(exc or '').lower()
    return '429' in text or 'too many requests' in text or 'rate limit' in text or 'ratelimit' in text


def _is_due(block_name: str) -> bool:
    retry_at = _parse_dt(_block_state.get(block_name, {}).get('next_retry_at'))
    return not retry_at or retry_at <= _now()


def _next_due_block(preferred: list[str] | None = None) -> str | None:
    global _scheduler_index
    blocks = preferred or _enabled_blocks()
    if not blocks:
        return None
    for _ in range(len(blocks)):
        block = blocks[_scheduler_index % len(blocks)]
        _scheduler_index += 1
        if _is_due(block):
            return block
    return None


def _make_client() -> WildberriesClient:
    return WildberriesClient(
        settings.wb_api_token,
        max_retries=getattr(settings, 'wb_retry_attempts', 2),
        base_delay_seconds=getattr(settings, 'wb_retry_base_delay_seconds', 5),
        request_pause_seconds=getattr(settings, 'wb_request_pause_seconds', 2),
        request_timeout_seconds=getattr(settings, 'wb_request_timeout_seconds', 20),
    )


def _mark_review_stale_unanswered(db: Session, current_ids: set[str], sync_run_id: str) -> int:
    q = db.query(Review).filter(Review.platform == 'WB', Review.operational_status == 'needs_response', Review.source_status == 'wb_unanswered')
    changed = 0
    for r in q.all():
        if r.external_id not in current_ids:
            r.operational_status = 'stale_unanswered'
            r.source_status = 'stale_unanswered'
            r.publish_blocked_reason = 'WB больше не вернул этот отзыв в актуальной очереди “Ждут ответа”.'
            r.last_seen_sync_run_id = sync_run_id
            changed += 1
    if changed:
        db.commit()
    return changed


def _mark_question_stale_unanswered(db: Session, current_ids: set[str], sync_run_id: str) -> int:
    q = db.query(Question).filter(Question.platform == 'WB', Question.operational_status == 'needs_response', Question.source_status == 'wb_unanswered')
    changed = 0
    for x in q.all():
        if x.external_id not in current_ids:
            x.operational_status = 'stale_unanswered'
            x.source_status = 'stale_unanswered'
            x.publish_blocked_reason = 'WB больше не вернул этот вопрос в актуальной очереди “Ждут ответа”.'
            x.last_seen_sync_run_id = sync_run_id
            changed += 1
    if changed:
        db.commit()
    return changed


def _upsert_review(db: Session, data: dict[str, Any]) -> str:
    existing = db.query(Review).filter(Review.platform == data['platform'], Review.external_id == data['external_id']).first()
    if settings.ai_auto_classify_on_sync:
        local = classify_review(data.get('text'), data.get('rating'), data.get('pros'), data.get('cons'))
        data.setdefault('ai_category', local.get('category'))
        data.setdefault('ai_sentiment', local.get('sentiment'))
        data.setdefault('ai_risk_level', local.get('risk_level'))
        data.setdefault('ai_can_autopublish', False)
        data.setdefault('ai_reason', local.get('reason'))
        data.setdefault('ai_tags', local.get('tags', []))
    if not existing:
        db.add(Review(**data))
        db.commit()
        return 'created'
    preserved_origin = existing.response_origin if existing.response_origin in {'auto_app', 'manual_app'} else None
    for key in ['sku','product_name','rating','text','pros','cons','client_name','created_at_marketplace','has_answer','raw','source_status','operational_status','last_seen_source','last_seen_sync_run_id','last_seen_at','publish_blocked_reason','response_origin','ai_tags']:
        if key in data:
            setattr(existing, key, data.get(key))
    if preserved_origin:
        existing.response_origin = preserved_origin
    if data.get('final_answer'):
        existing.final_answer = data.get('final_answer')
        existing.has_answer = True
        if not preserved_origin:
            existing.response_origin = 'seller_cabinet'
    if settings.ai_auto_classify_on_sync and not existing.ai_category:
        existing.ai_category = data.get('ai_category')
        existing.ai_sentiment = data.get('ai_sentiment')
        existing.ai_risk_level = data.get('ai_risk_level')
        existing.ai_can_autopublish = bool(data.get('ai_can_autopublish'))
        existing.ai_reason = data.get('ai_reason')
    if existing.has_answer:
        existing.status = 'answered_on_marketplace'
        if existing.operational_status == 'needs_response':
            existing.operational_status = 'analytics_only'
    db.commit()
    return 'updated'


def _upsert_question(db: Session, data: dict[str, Any]) -> str:
    existing = db.query(Question).filter(Question.platform == data['platform'], Question.external_id == data['external_id']).first()
    if settings.ai_auto_classify_on_sync:
        local = classify_question(data.get('text'))
        data.setdefault('ai_category', local.get('category'))
        data.setdefault('ai_risk_level', local.get('risk_level'))
        data.setdefault('ai_can_autopublish', False)
        data.setdefault('ai_reason', local.get('reason'))
        data.setdefault('ai_tags', local.get('tags', []))
    if not existing:
        db.add(Question(**data))
        db.commit()
        return 'created'
    preserved_origin = existing.response_origin if existing.response_origin in {'auto_app', 'manual_app'} else None
    for key in ['sku','product_name','text','client_name','created_at_marketplace','has_answer','raw','source_status','operational_status','last_seen_source','last_seen_sync_run_id','last_seen_at','publish_blocked_reason','response_origin','ai_tags']:
        if key in data:
            setattr(existing, key, data.get(key))
    if preserved_origin:
        existing.response_origin = preserved_origin
    if data.get('final_answer'):
        existing.final_answer = data.get('final_answer')
        existing.has_answer = True
        if not preserved_origin:
            existing.response_origin = 'seller_cabinet'
    if settings.ai_auto_classify_on_sync and not existing.ai_category:
        existing.ai_category = data.get('ai_category')
        existing.ai_risk_level = data.get('ai_risk_level')
        existing.ai_can_autopublish = bool(data.get('ai_can_autopublish'))
        existing.ai_reason = data.get('ai_reason')
    if existing.has_answer:
        existing.status = 'answered_on_marketplace'
        if existing.operational_status == 'needs_response':
            existing.operational_status = 'analytics_only'
    db.commit()
    return 'updated'


def _snapshot_product_ratings(db: Session) -> int:
    rows = db.query(Review.platform, Review.sku, Review.product_name).filter(Review.sku.isnot(None)).distinct().all()
    created = 0
    for platform, sku, product_name in rows:
        reviews = db.query(Review).filter(Review.platform == platform, Review.sku == sku, Review.rating.isnot(None)).all()
        ratings = [r.rating for r in reviews if r.rating is not None]
        if not ratings:
            continue
        avg = sum(ratings) / len(ratings)
        db.add(RatingSnapshot(platform=platform, sku=sku, product_name=product_name, rating=f'{avg:.2f}', feedbacks_count=len(ratings), raw={'source':'local_reviews'}))
        created += 1
    db.commit()
    return created


async def _fetch_one_page(block_name: str, fetcher: Callable[[int, int], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    take = max(1, int(getattr(settings, 'wb_sync_take', 50)))
    state = _block_state.setdefault(block_name, _base_block_state(block_name))
    page = int(state.get('next_page') or 0) if block_name in HISTORICAL_BLOCKS else 0
    skip = page * take
    _set_progress(f'WB {block_name}: page {page + 1}', take=take, skip=skip)
    items = await fetcher(take, skip)
    state['last_page'] = page
    state['last_skip'] = skip
    state['last_received'] = len(items)
    if block_name in HISTORICAL_BLOCKS:
        if len(items) >= take:
            state['next_page'] = page + 1
            state['finished'] = False
        else:
            state['next_page'] = 0
            state['finished'] = True
            state['finished_at'] = _now_iso()
    else:
        state['next_page'] = 0
    _save_state()
    return items


async def _import_reviews_block(db: Session, label: str, source: str, fetcher: Callable[[int,int], Awaitable[list[dict[str, Any]]]], sync_run_id: str, *, source_status: str, operational_status: str, has_answer_override: bool | None = None) -> tuple[dict[str, Any], set[str]]:
    block = {'label': label, 'status': 'pending', 'received': 0, 'created': 0, 'updated': 0, 'error': None}
    ids: set[str] = set()
    items = await _fetch_one_page(label, fetcher)
    block['received'] = len(items)
    for item in items:
        data = normalize_feedback(item, source=source)
        if not data.get('external_id') or data.get('external_id') == 'None':
            continue
        if has_answer_override is not None:
            data['has_answer'] = bool(has_answer_override or data.get('final_answer'))
        data['source_status'] = source_status
        data['operational_status'] = 'analytics_only' if data.get('has_answer') else operational_status
        data['last_seen_source'] = label
        data['last_seen_sync_run_id'] = sync_run_id
        data['last_seen_at'] = datetime.utcnow()
        data['publish_blocked_reason'] = None if data['operational_status'] == 'needs_response' else 'Ответ уже есть или запись не в очереди “Ждут ответа”.'
        data['response_origin'] = 'seller_cabinet' if data.get('has_answer') and data.get('final_answer') else None
        ids.add(data['external_id'])
        res = _upsert_review(db, data)
        block['created' if res == 'created' else 'updated'] += 1
    block['current_ids'] = len(ids)
    block['status'] = 'success'
    return block, ids


async def _import_questions_block(db: Session, label: str, fetcher: Callable[[int,int], Awaitable[list[dict[str, Any]]]], sync_run_id: str, *, source_status: str, operational_status: str, has_answer_override: bool | None = None) -> tuple[dict[str, Any], set[str]]:
    block = {'label': label, 'status': 'pending', 'received': 0, 'created': 0, 'updated': 0, 'error': None}
    ids: set[str] = set()
    items = await _fetch_one_page(label, fetcher)
    block['received'] = len(items)
    for item in items:
        data = normalize_question(item)
        if not data.get('external_id') or data.get('external_id') == 'None':
            continue
        if has_answer_override is not None:
            data['has_answer'] = bool(has_answer_override or data.get('final_answer'))
        data['source_status'] = source_status
        data['operational_status'] = 'analytics_only' if data.get('has_answer') else operational_status
        data['last_seen_source'] = label
        data['last_seen_sync_run_id'] = sync_run_id
        data['last_seen_at'] = datetime.utcnow()
        data['publish_blocked_reason'] = None if data['operational_status'] == 'needs_response' else 'Ответ уже есть или запись не в очереди “Ждут ответа”.'
        data['response_origin'] = 'seller_cabinet' if data.get('has_answer') and data.get('final_answer') else None
        ids.add(data['external_id'])
        res = _upsert_question(db, data)
        block['created' if res == 'created' else 'updated'] += 1
    block['current_ids'] = len(ids)
    block['status'] = 'success'
    return block, ids


async def sync_wb_block(db: Session, block_name: str) -> dict[str, Any]:
    if block_name not in WB_SYNC_BLOCKS:
        raise ValueError(f'Неизвестный блок синхронизации: {block_name}')
    client = _make_client()
    sync_run_id = _now_iso()
    diagnostics = {'blocks': {}, 'warnings': [], 'scheduler': 'rc1.1_per_block'}
    result = {
        'platform': 'WB', 'sync_mode': settings.effective_wb_sync_mode(), 'sync_run_id': sync_run_id,
        'block': block_name, 'imported_reviews': 0, 'updated_reviews': 0, 'imported_questions': 0,
        'updated_questions': 0, 'rating_snapshots_created': 0, 'diagnostics': diagnostics,
        'message': f'Блок WB {block_name} завершен',
    }

    if block_name == 'feedbacks_unanswered':
        block, ids = await _import_reviews_block(db, block_name, 'unanswered', lambda take, skip: client.get_feedbacks(False, take, skip), sync_run_id, source_status='wb_unanswered', operational_status='needs_response', has_answer_override=False)
        diagnostics['blocks'][block_name] = block
        result['imported_reviews'], result['updated_reviews'] = block['created'], block['updated']
        diagnostics['reviews_stale_unanswered_marked'] = _mark_review_stale_unanswered(db, ids, sync_run_id)
    elif block_name == 'questions_unanswered':
        block, ids = await _import_questions_block(db, block_name, lambda take, skip: client.get_questions(False, take, skip), sync_run_id, source_status='wb_unanswered', operational_status='needs_response', has_answer_override=False)
        diagnostics['blocks'][block_name] = block
        result['imported_questions'], result['updated_questions'] = block['created'], block['updated']
        diagnostics['questions_stale_unanswered_marked'] = _mark_question_stale_unanswered(db, ids, sync_run_id)
    elif block_name == 'feedbacks_answered':
        block, _ = await _import_reviews_block(db, block_name, 'answered', lambda take, skip: client.get_feedbacks(True, take, skip), sync_run_id, source_status='wb_answered', operational_status='analytics_only', has_answer_override=True)
        diagnostics['blocks'][block_name] = block
        result['imported_reviews'], result['updated_reviews'] = block['created'], block['updated']
    elif block_name == 'questions_answered':
        block, _ = await _import_questions_block(db, block_name, lambda take, skip: client.get_questions(True, take, skip), sync_run_id, source_status='wb_answered', operational_status='analytics_only', has_answer_override=True)
        diagnostics['blocks'][block_name] = block
        result['imported_questions'], result['updated_questions'] = block['created'], block['updated']
    elif block_name == 'feedbacks_archive':
        block, _ = await _import_reviews_block(db, block_name, 'archive', lambda take, skip: client.get_feedbacks_archive(take, skip), sync_run_id, source_status='wb_archive', operational_status='analytics_only', has_answer_override=True)
        diagnostics['blocks'][block_name] = block
        result['imported_reviews'], result['updated_reviews'] = block['created'], block['updated']

    result['rating_snapshots_created'] = _snapshot_product_ratings(db)
    return result


async def run_sync_wb_block_with_status(block_name: str | None = None, db: Session | None = None, source: str = 'manual') -> dict[str, Any]:
    if _sync_lock.locked():
        return {'platform': 'WB', 'skipped': True, 'message': 'Синхронизация WB уже выполняется. Новый запуск пропущен.'}
    if block_name in (None, 'next'):
        block_name = _next_due_block()
        if not block_name:
            return {'platform': 'WB', 'skipped': True, 'message': 'Все блоки WB сейчас на индивидуальном cooldown.'}
    if not _is_due(block_name) and source != 'manual_force':
        return {'platform': 'WB', 'skipped': True, 'block': block_name, 'message': f'Блок {block_name} на паузе до {_block_state[block_name].get("next_retry_at")}', 'state': _block_state[block_name]}

    async with _sync_lock:
        own_session = db is None
        session = db or SessionLocal()
        state = _block_state.setdefault(block_name, _base_block_state(block_name))
        state.update({'status': 'running', 'last_started_at': _now_iso(), 'last_error': None})
        _sync_status.update({'running': True, 'last_started_at': _now_iso(), 'last_error': None, 'current_block': block_name, 'source': source})
        _set_progress(f'WB scheduler: {block_name}')
        try:
            result = await asyncio.wait_for(sync_wb_block(session, block_name), timeout=getattr(settings, 'wb_sync_max_runtime_seconds', 900))
            result['source'] = source
            state.update({'status': 'success', 'last_success_at': _now_iso(), 'next_retry_at': None, 'last_result': result, 'last_error': None})
            _sync_status['last_result'] = result
            _sync_status['last_success_at'] = _now_iso()
            _save_state()
            return result
        except Exception as exc:
            seconds = _cooldown_seconds_from_error(exc)
            if _is_rate_limit_error(exc):
                state['status'] = 'rate_limited'
                state['next_retry_at'] = (_now() + timedelta(seconds=seconds)).isoformat()
                state['cooldown_source'] = 'block_only'
                state['rate_limited_count'] = int(state.get('rate_limited_count') or 0) + 1
            else:
                state['status'] = 'failed'
                state['next_retry_at'] = (_now() + timedelta(seconds=60)).isoformat()
            state['last_error'] = str(exc)
            _sync_status['last_error'] = str(exc)
            _sync_status['last_result'] = {'platform': 'WB', 'block': block_name, 'failed': True, 'error': str(exc), 'block_state': state}
            _save_state()
            # Do not raise in auto/manual-next mode: service must continue and UI must get diagnostics.
            return _sync_status['last_result']
        finally:
            state['last_finished_at'] = _now_iso()
            _sync_status['running'] = False
            _sync_status['last_finished_at'] = _now_iso()
            if own_session:
                session.close()


async def run_sync_wb_with_status(db: Session | None = None, source: str = 'manual') -> dict[str, Any]:
    return await run_sync_wb_block_with_status('next', db=db, source=source)


async def run_sync_wb_operational_once(db: Session | None = None, source: str = 'manual_operational') -> dict[str, Any]:
    block = _next_due_block(WB_OPERATIONAL_BLOCKS)
    if not block:
        return {'platform': 'WB', 'skipped': True, 'scheduler': 'operational', 'message': 'Операционные блоки WB сейчас на cooldown.'}
    return await run_sync_wb_block_with_status(block, db=db, source=source)


async def run_sync_wb_backfill_once(db: Session | None = None, source: str = 'manual_backfill') -> dict[str, Any]:
    block = _next_due_block(WB_BACKFILL_BLOCKS)
    if not block:
        return {'platform': 'WB', 'skipped': True, 'scheduler': 'backfill', 'message': 'Исторические блоки WB сейчас на cooldown.'}
    return await run_sync_wb_block_with_status(block, db=db, source=source)


async def wb_auto_sync_loop() -> None:
    if not getattr(settings, 'wb_api_token', None):
        _sync_status['last_error'] = 'WB_API_TOKEN is empty; WB auto sync is not started.'
        return
    if not settings.wb_auto_sync_enabled:
        _sync_status['last_error'] = 'WB auto sync disabled.'
        return
    await asyncio.sleep(max(0, int(getattr(settings, 'wb_auto_sync_initial_delay_seconds', 20))))
    while True:
        try:
            await run_sync_wb_block_with_status('next', source='auto_rc11_per_block')
        except Exception as exc:
            _sync_status['last_error'] = str(exc)
        await asyncio.sleep(max(60, int(getattr(settings, 'wb_auto_sync_interval_seconds', 120))))


def get_sync_status() -> dict[str, Any]:
    _sync_status['auto_sync_enabled'] = settings.wb_auto_sync_enabled
    _sync_status['strategy'] = 'rc1.1_per_block_scheduler_no_global_cooldown'
    _sync_status['interval_seconds'] = getattr(settings, 'wb_auto_sync_interval_seconds', None)
    _sync_status['sync_mode'] = settings.effective_wb_sync_mode()
    _sync_status['take'] = getattr(settings, 'wb_sync_take', None)
    _sync_status['request_pause_seconds'] = getattr(settings, 'wb_request_pause_seconds', None)
    _sync_status['rate_limit_cooldown_seconds'] = getattr(settings, 'wb_rate_limit_cooldown_seconds', None)
    _sync_status['enabled_blocks'] = _enabled_blocks()
    _sync_status['blocks_state'] = _block_state
    _sync_status['health_summary'] = {
        name: {
            'status': st.get('status'),
            'last_received': st.get('last_received'),
            'last_success_at': st.get('last_success_at'),
            'next_retry_at': st.get('next_retry_at'),
            'last_error': st.get('last_error'),
            'next_page': st.get('next_page'),
        }
        for name, st in _block_state.items()
    }
    return dict(_sync_status)

# Compatibility for old imports
async def sync_wb(db: Session) -> dict[str, Any]:
    return await run_sync_wb_with_status(db=db, source='legacy_sync_wb')
