from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import Review, Question, RatingSnapshot
from ..marketplace_clients.wb import WildberriesClient, normalize_feedback, normalize_question
from ..ai.rule_based import classify_review, classify_question

_sync_lock = asyncio.Lock()
_sync_status: dict[str, Any] = {
    'auto_sync_enabled': settings.wb_auto_sync_enabled,
    'interval_seconds': settings.wb_auto_sync_interval_seconds,
    'initial_delay_seconds': settings.wb_auto_sync_initial_delay_seconds,
    'sync_mode': settings.effective_wb_sync_mode(),
    'running': False,
    'last_started_at': None,
    'last_finished_at': None,
    'last_success_at': None,
    'last_error': None,
    'last_result': None,
    'progress': None,
}

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



def _mark_review_stale_unanswered(db: Session, current_ids: set[str], sync_run_id: str) -> int:
    q = db.query(Review).filter(
        Review.platform == 'WB',
        Review.operational_status == 'needs_response',
        Review.source_status == 'wb_unanswered',
    )
    changed = 0
    for r in q.all():
        if r.external_id not in current_ids:
            r.operational_status = 'stale_unanswered'
            r.source_status = 'stale_unanswered'
            r.publish_blocked_reason = 'WB больше не вернул этот отзыв в актуальной очереди “Ждут ответа”. Публикация заблокирована, чтобы не ответить повторно.'
            r.last_seen_sync_run_id = sync_run_id
            changed += 1
    if changed:
        db.commit()
    return changed

def _mark_question_stale_unanswered(db: Session, current_ids: set[str], sync_run_id: str) -> int:
    q = db.query(Question).filter(
        Question.platform == 'WB',
        Question.operational_status == 'needs_response',
        Question.source_status == 'wb_unanswered',
    )
    changed = 0
    for x in q.all():
        if x.external_id not in current_ids:
            x.operational_status = 'stale_unanswered'
            x.source_status = 'stale_unanswered'
            x.publish_blocked_reason = 'WB больше не вернул этот вопрос в актуальной очереди “Ждут ответа”. Публикация заблокирована, чтобы не ответить повторно.'
            x.last_seen_sync_run_id = sync_run_id
            changed += 1
    if changed:
        db.commit()
    return changed

def _set_progress(step: str, **extra: Any) -> None:
    _sync_status['progress'] = {'step': step, 'at': _now_iso(), **extra}

def _answer_states_from_settings() -> list[bool]:
    mode = settings.effective_wb_sync_mode()
    if mode == 'unanswered':
        return [False]
    if mode == 'answered':
        return [True]
    return [False, True]

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

    preserved_response_origin = existing.response_origin if existing.response_origin in {'auto_app', 'manual_app'} and not data.get('has_answer') else None
    for key in ['sku', 'product_name', 'rating', 'text', 'pros', 'cons', 'client_name', 'created_at_marketplace', 'has_answer', 'raw', 'source_status', 'operational_status', 'last_seen_source', 'last_seen_sync_run_id', 'last_seen_at', 'publish_blocked_reason', 'response_origin', 'ai_tags']:
        setattr(existing, key, data.get(key))
    if preserved_response_origin:
        existing.response_origin = preserved_response_origin
    if data.get('final_answer'):
        existing.final_answer = data.get('final_answer')
        existing.draft_answer = data.get('final_answer')
    if data.get('has_answer') and not existing.final_answer:
        existing.final_answer = existing.final_answer or data.get('final_answer')
    if settings.ai_auto_classify_on_sync and not existing.ai_category:
        existing.ai_category = data.get('ai_category')
        existing.ai_sentiment = data.get('ai_sentiment')
        existing.ai_risk_level = data.get('ai_risk_level')
        existing.ai_can_autopublish = bool(data.get('ai_can_autopublish'))
        existing.ai_reason = data.get('ai_reason')
    if existing.has_answer:
        existing.status = 'answered_on_marketplace'
        existing.ai_can_autopublish = False
    elif existing.operational_status != 'needs_response' and existing.status in {'new', 'ready_to_review', 'ready_to_publish'}:
        existing.status = 'status_changed'
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

    preserved_response_origin = existing.response_origin if existing.response_origin in {'auto_app', 'manual_app'} and not data.get('has_answer') else None
    for key in ['sku', 'product_name', 'text', 'client_name', 'created_at_marketplace', 'has_answer', 'raw', 'source_status', 'operational_status', 'last_seen_source', 'last_seen_sync_run_id', 'last_seen_at', 'publish_blocked_reason', 'response_origin', 'ai_tags']:
        setattr(existing, key, data.get(key))
    if preserved_response_origin:
        existing.response_origin = preserved_response_origin
    if data.get('final_answer'):
        existing.final_answer = data.get('final_answer')
        existing.draft_answer = data.get('final_answer')
    if data.get('has_answer') and not existing.final_answer:
        existing.final_answer = existing.final_answer or data.get('final_answer')
    if settings.ai_auto_classify_on_sync and not existing.ai_category:
        existing.ai_category = data.get('ai_category')
        existing.ai_risk_level = data.get('ai_risk_level')
        existing.ai_can_autopublish = bool(data.get('ai_can_autopublish'))
        existing.ai_reason = data.get('ai_reason')
    if existing.has_answer:
        existing.status = 'answered_on_marketplace'
        existing.ai_can_autopublish = False
    elif existing.operational_status != 'needs_response' and existing.status in {'new', 'ready_to_review', 'ready_to_publish'}:
        existing.status = 'status_changed'
    db.commit()
    return 'updated'

async def _fetch_pages(fetcher: Callable[[int, int], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Legacy helper: fetch from the first page. Kept for old diagnostic functions."""
    take = max(1, int(settings.wb_sync_take))
    max_pages = max(1, int(getattr(settings, 'wb_sync_max_pages', 3)))
    all_items: list[dict[str, Any]] = []
    for page in range(max_pages):
        skip = page * take
        items = await fetcher(take, skip)
        all_items.extend(items)
        if len(items) < take:
            break
        await asyncio.sleep(settings.wb_request_pause_seconds)
    return all_items


# v2.3: one-block/one-page drip backfill.
# WB has a global limiter per seller. Pulling all pages of answered feedbacks, archive and questions
# in one run caused 429 and timeouts. For operational queues we always read page 0. For historical
# blocks we keep a cursor and gradually move through pages across scheduler cycles.
HISTORICAL_BLOCKS = {'feedbacks_answered', 'questions_answered', 'feedbacks_archive'}

async def _fetch_pages_for_block(block_name: str, fetcher: Callable[[int, int], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    take = max(1, int(settings.wb_sync_take))
    max_pages_total = max(1, int(getattr(settings, 'wb_sync_max_pages', 20)))
    pages_per_run = max(1, int(getattr(settings, 'wb_sync_pages_per_block_run', 1)))
    pages_per_run = min(pages_per_run, max_pages_total)

    state = _block_state.setdefault(block_name, {})
    if block_name in HISTORICAL_BLOCKS:
        start_page = int(state.get('next_page', 0) or 0)
    else:
        start_page = 0

    all_items: list[dict[str, Any]] = []
    last_page_full = False
    for i in range(pages_per_run):
        page = start_page + i
        if page >= max_pages_total:
            page = 0
        skip = page * take
        _set_progress(f'WB: {block_name} page {page + 1}/{max_pages_total}', take=take, skip=skip)
        items = await fetcher(take, skip)
        all_items.extend(items)
        last_page_full = len(items) >= take
        await asyncio.sleep(settings.wb_request_pause_seconds)
        if len(items) < take:
            break

    if block_name in HISTORICAL_BLOCKS:
        if all_items and last_page_full:
            state['next_page'] = start_page + pages_per_run
            if state['next_page'] >= max_pages_total:
                state['next_page'] = 0
        else:
            state['next_page'] = 0
        state['last_skip'] = start_page * take
        state['pages_per_run'] = pages_per_run
    else:
        state['next_page'] = 0

    return all_items
async def _safe_optional(label: str, coro_factory):
    try:
        _set_progress(f"WB: {label}")
        return await coro_factory()
    except Exception as exc:
        return {"error": str(exc)}

async def _safe_fetch(label: str, diagnostics: dict[str, Any], key: str, fetcher: Callable[[int, int], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    try:
        _set_progress(f"WB: {label}")
        items = await _fetch_pages_for_block(label, fetcher)
        diagnostics[key] = len(items)
        return items
    except Exception as exc:
        diagnostics.setdefault("warnings", []).append(f"{label}: {exc}")
        diagnostics[key] = 0
        return []


def _new_block_status(label: str) -> dict[str, Any]:
    return {'label': label, 'status': 'pending', 'received': 0, 'created': 0, 'updated': 0, 'error': None}

async def _import_reviews_block(db: Session, label: str, source: str, fetcher: Callable[[int, int], Awaitable[list[dict[str, Any]]]], diagnostics: dict[str, Any], sync_run_id: str, *, source_status: str, operational_status: str, has_answer_override: bool | None = None) -> tuple[int, int, set[str]]:
    block = _new_block_status(label)
    diagnostics.setdefault('blocks', {})[label] = block
    created = 0
    updated = 0
    current_ids: set[str] = set()
    try:
        _set_progress(f'WB: {label}')
        items = await _fetch_pages_for_block(label, fetcher)
        block['received'] = len(items)
        for item in items:
            data = normalize_feedback(item, source=source)
            if not data.get('external_id') or data.get('external_id') == 'None':
                continue
            if has_answer_override is not None:
                # Do not lose answers that already exist in the seller cabinet.
                # Some WB list endpoints may still return answered items in the operational stream.
                data['has_answer'] = bool(has_answer_override or data.get('has_answer') or data.get('final_answer'))
            effective_source_status = source_status
            effective_operational_status = operational_status
            if data.get('has_answer') or data.get('final_answer'):
                effective_source_status = 'wb_answered' if source_status != 'wb_archive' else 'wb_archive'
                effective_operational_status = 'analytics_only'
                data['response_origin'] = data.get('response_origin') or 'seller_cabinet'
                data['publish_blocked_reason'] = 'Ответ уже есть в личном кабинете WB; новый AI-ответ не генерируется.'
            data['source_status'] = effective_source_status
            data['operational_status'] = effective_operational_status
            data['last_seen_source'] = label
            data['last_seen_sync_run_id'] = sync_run_id
            data['last_seen_at'] = datetime.utcnow()
            if not data.get('publish_blocked_reason'):
                data['publish_blocked_reason'] = None if effective_operational_status == 'needs_response' else 'Не находится в актуальной очереди WB “Ждут ответа”; публикация из этого раздела заблокирована.'
            data['response_origin'] = data.get('response_origin') or ('seller_cabinet' if data.get('has_answer') else None)
            current_ids.add(data['external_id'])
            result = _upsert_review(db, data)
            if result == 'created':
                created += 1
            else:
                updated += 1
        block['created'] = created
        block['updated'] = updated
        block['current_ids'] = len(current_ids)
        block['status'] = 'success'
        return created, updated, current_ids
    except Exception as exc:
        block['status'] = 'failed'
        block['error'] = str(exc)
        diagnostics.setdefault('warnings', []).append(f'{label}: {exc}')
        return 0, 0, current_ids

async def _import_questions_block(db: Session, label: str, fetcher: Callable[[int, int], Awaitable[list[dict[str, Any]]]], diagnostics: dict[str, Any], sync_run_id: str, *, source_status: str, operational_status: str, has_answer_override: bool | None = None) -> tuple[int, int, set[str]]:
    block = _new_block_status(label)
    diagnostics.setdefault('blocks', {})[label] = block
    created = 0
    updated = 0
    current_ids: set[str] = set()
    try:
        _set_progress(f'WB: {label}')
        items = await _fetch_pages_for_block(label, fetcher)
        block['received'] = len(items)
        for item in items:
            data = normalize_question(item)
            if not data.get('external_id') or data.get('external_id') == 'None':
                continue
            if has_answer_override is not None:
                # Do not lose answers that already exist in the seller cabinet.
                data['has_answer'] = bool(has_answer_override or data.get('has_answer') or data.get('final_answer'))
            effective_source_status = source_status
            effective_operational_status = operational_status
            if data.get('has_answer') or data.get('final_answer'):
                effective_source_status = 'wb_answered'
                effective_operational_status = 'analytics_only'
                data['response_origin'] = data.get('response_origin') or 'seller_cabinet'
                data['publish_blocked_reason'] = 'Ответ уже есть в личном кабинете WB; новый AI-ответ не генерируется.'
            data['source_status'] = effective_source_status
            data['operational_status'] = effective_operational_status
            data['last_seen_source'] = label
            data['last_seen_sync_run_id'] = sync_run_id
            data['last_seen_at'] = datetime.utcnow()
            if not data.get('publish_blocked_reason'):
                data['publish_blocked_reason'] = None if effective_operational_status == 'needs_response' else 'Не находится в актуальной очереди WB “Ждут ответа”; публикация из этого раздела заблокирована.'
            data['response_origin'] = data.get('response_origin') or ('seller_cabinet' if data.get('has_answer') else None)
            current_ids.add(data['external_id'])
            result = _upsert_question(db, data)
            if result == 'created':
                created += 1
            else:
                updated += 1
        block['created'] = created
        block['updated'] = updated
        block['current_ids'] = len(current_ids)
        block['status'] = 'success'
        return created, updated, current_ids
    except Exception as exc:
        block['status'] = 'failed'
        block['error'] = str(exc)
        diagnostics.setdefault('warnings', []).append(f'{label}: {exc}')
        return 0, 0, current_ids

def _snapshot_product_ratings(db: Session) -> int:
    """Local rating snapshots from synced reviews. Later this can be replaced by marketplace product-card rating APIs."""
    rows = db.query(Review.platform, Review.sku, Review.product_name).filter(Review.sku.isnot(None)).distinct().all()
    created = 0
    for platform, sku, product_name in rows:
        reviews = db.query(Review).filter(Review.platform == platform, Review.sku == sku, Review.rating.isnot(None)).all()
        if not reviews:
            continue
        ratings = [r.rating for r in reviews if r.rating is not None]
        avg = sum(ratings) / len(ratings)
        db.add(RatingSnapshot(
            platform=platform,
            sku=sku,
            product_name=product_name,
            rating=f"{avg:.2f}",
            feedbacks_count=len(ratings),
            raw={
                "source": "local_reviews",
                "negative_count": sum(1 for x in ratings if x <= 3),
                "positive_count": sum(1 for x in ratings if x >= 4),
            }
        ))
        created += 1
    db.commit()
    return created

async def sync_wb(db: Session) -> dict:
    client = WildberriesClient(
        settings.wb_api_token,
        max_retries=settings.wb_retry_attempts,
        base_delay_seconds=settings.wb_retry_base_delay_seconds,
        request_pause_seconds=settings.wb_request_pause_seconds,
        request_timeout_seconds=settings.wb_request_timeout_seconds,
    )

    imported_reviews = 0
    updated_reviews = 0
    imported_questions = 0
    updated_questions = 0
    states = _answer_states_from_settings()
    sync_run_id = _now_iso()

    diagnostics: dict[str, Any] = {
        'feedbacks_unanswered_received': 0,
        'feedbacks_answered_received': 0,
        'feedbacks_archive_received': 0,
        'questions_unanswered_received': 0,
        'questions_answered_received': 0,
        'feedbacks_count_unanswered_api': None,
        'feedbacks_count_answered_api': None,
        'feedbacks_count_api': None,
        'questions_count_unanswered_api': None,
        'questions_count_api': None,
        'blocks': {},
        'warnings': [],
    }

    # v1.0: не начинаем синхронизацию с count endpoints.
    # Они не нужны для импорта и у WB могут зависать/отдавать 429.
    # Сначала грузим реальные списки: unanswered -> questions -> answered -> archive.
    diagnostics['warnings'].append('Count endpoints не используются перед импортом: сначала грузим реальные списки WB.')

    def _publish_partial(stage: str) -> None:
        _sync_status['last_result'] = {
            'platform': 'WB',
            'sync_mode': settings.effective_wb_sync_mode(),
            'sync_run_id': sync_run_id,
            'stage': stage,
            'partial': True,
            'imported_reviews': imported_reviews,
            'updated_reviews': updated_reviews,
            'imported_questions': imported_questions,
            'updated_questions': updated_questions,
            'diagnostics': diagnostics,
        }


    if False in states:
        created, updated, current_unanswered_review_ids = await _import_reviews_block(
            db,
            'feedbacks_unanswered',
            'unanswered',
            lambda take, skip: client.get_feedbacks(is_answered=False, take=take, skip=skip),
            diagnostics,
            sync_run_id,
            source_status='wb_unanswered',
            operational_status='needs_response',
            has_answer_override=False,
        )
        imported_reviews += created
        updated_reviews += updated
        diagnostics['feedbacks_unanswered_received'] = diagnostics['blocks']['feedbacks_unanswered']['received']
        if diagnostics['blocks']['feedbacks_unanswered']['status'] == 'success':
            diagnostics['reviews_stale_unanswered_marked'] = _mark_review_stale_unanswered(db, current_unanswered_review_ids, sync_run_id)
        _publish_partial('feedbacks_unanswered_done')
        await asyncio.sleep(settings.wb_request_pause_seconds)

        created, updated, current_unanswered_question_ids = await _import_questions_block(
            db,
            'questions_unanswered',
            lambda take, skip: client.get_questions(is_answered=False, take=take, skip=skip),
            diagnostics,
            sync_run_id,
            source_status='wb_unanswered',
            operational_status='needs_response',
            has_answer_override=False,
        )
        imported_questions += created
        updated_questions += updated
        diagnostics['questions_unanswered_received'] = diagnostics['blocks']['questions_unanswered']['received']
        if diagnostics['blocks']['questions_unanswered']['status'] == 'success':
            diagnostics['questions_stale_unanswered_marked'] = _mark_question_stale_unanswered(db, current_unanswered_question_ids, sync_run_id)
        _publish_partial('questions_unanswered_done')
        await asyncio.sleep(settings.wb_request_pause_seconds)

    if True in states:
        created, updated, _ = await _import_reviews_block(
            db,
            'feedbacks_answered',
            'answered',
            lambda take, skip: client.get_feedbacks(is_answered=True, take=take, skip=skip),
            diagnostics,
            sync_run_id,
            source_status='wb_answered',
            operational_status='analytics_only',
            has_answer_override=True,
        )
        imported_reviews += created
        updated_reviews += updated
        diagnostics['feedbacks_answered_received'] = diagnostics['blocks']['feedbacks_answered']['received']
        _publish_partial('feedbacks_answered_done')
        await asyncio.sleep(settings.wb_request_pause_seconds)

        created, updated, _ = await _import_questions_block(
            db,
            'questions_answered',
            lambda take, skip: client.get_questions(is_answered=True, take=take, skip=skip),
            diagnostics,
            sync_run_id,
            source_status='wb_answered',
            operational_status='analytics_only',
            has_answer_override=True,
        )
        imported_questions += created
        updated_questions += updated
        diagnostics['questions_answered_received'] = diagnostics['blocks']['questions_answered']['received']
        _publish_partial('questions_answered_done')
        await asyncio.sleep(settings.wb_request_pause_seconds)

        created, updated, _ = await _import_reviews_block(
            db,
            'feedbacks_archive',
            'archive',
            lambda take, skip: client.get_feedbacks_archive(take=take, skip=skip),
            diagnostics,
            sync_run_id,
            source_status='wb_archive',
            operational_status='analytics_only',
            has_answer_override=True,
        )
        imported_reviews += created
        updated_reviews += updated
        diagnostics['feedbacks_archive_received'] = diagnostics['blocks']['feedbacks_archive']['received']
        _publish_partial('feedbacks_archive_done')

    # Optional diagnostics AFTER import. They never define the working queue.
    if settings.wb_feedback_count_diagnostics_enabled:
        diagnostics['feedbacks_count_unanswered_api'] = await _safe_optional('диагностика count отзывов без ответа', client.get_feedbacks_unanswered_count)
        await asyncio.sleep(settings.wb_request_pause_seconds)
        diagnostics['feedbacks_count_answered_api'] = await _safe_optional('диагностика count отзывов с ответом', lambda: client.get_feedbacks_count(True))
        await asyncio.sleep(settings.wb_request_pause_seconds)
        diagnostics['feedbacks_count_api'] = await _safe_optional('диагностика общий count отзывов', client.get_feedbacks_count)
        await asyncio.sleep(settings.wb_request_pause_seconds)
        _publish_partial('feedback_counts_done')

    if settings.wb_diagnostic_counts_enabled:
        diagnostics['questions_count_unanswered_api'] = await _safe_optional('диагностика count-unanswered вопросов', client.get_questions_unanswered_count)
        await asyncio.sleep(settings.wb_request_pause_seconds)
        diagnostics['questions_count_api'] = await _safe_optional('диагностика общий count вопросов', client.get_questions_count)
        await asyncio.sleep(settings.wb_request_pause_seconds)
        _publish_partial('question_counts_done')
    else:
        diagnostics['warnings'].append('Диагностические count endpoints вопросов WB отключены, чтобы не блокировать синхронизацию. Включить: WB_DIAGNOSTIC_COUNTS_ENABLED=true.')

    # Helpful diagnostics for the exact problem Lena saw.
    q_count_unanswered = diagnostics.get('questions_count_unanswered_api')
    if isinstance(q_count_unanswered, dict) and 'error' not in q_count_unanswered and diagnostics['questions_unanswered_received'] == 0:
        diagnostics['warnings'].append('WB count endpoint may show unanswered questions, but list endpoint returned 0. Check token category, cabinet/legal entity and WB response shape.')
    if diagnostics.get('feedbacks_count_answered_api') not in (None, {}) and diagnostics.get('feedbacks_answered_received', 0) == 0 and diagnostics.get('feedbacks_archive_received', 0) == 0:
        diagnostics['warnings'].append('WB answered/archive feedback counters may be non-zero, but list/archive returned 0. Check diagnostics.blocks for exact endpoint errors.')

    _set_progress('Аналитика: обновляем локальные снимки рейтингов по товарам')
    snapshots_created = _snapshot_product_ratings(db)

    return {
        'platform': 'WB',
        'sync_mode': settings.effective_wb_sync_mode(),
        'sync_run_id': sync_run_id,
        'take': settings.wb_sync_take,
        'max_pages': getattr(settings, 'wb_sync_max_pages', 3),
        'imported_reviews': imported_reviews,
        'updated_reviews': updated_reviews,
        'imported_questions': imported_questions,
        'updated_questions': updated_questions,
        'rating_snapshots_created': snapshots_created,
        'diagnostics': diagnostics,
        'message': 'Синхронизация WB завершена'
    }

async def run_sync_wb_with_status(db: Session | None = None, source: str = 'manual') -> dict:
    if _sync_lock.locked():
        return {
            'platform': 'WB',
            'skipped': True,
            'message': 'Синхронизация WB уже выполняется. Новый запуск пропущен, чтобы не ловить лимиты API.'
        }

    async with _sync_lock:
        own_session = db is None
        session = db or SessionLocal()
        _sync_status['running'] = True
        _sync_status['last_started_at'] = _now_iso()
        _sync_status['last_error'] = None
        _sync_status['source'] = source
        _sync_status['sync_mode'] = settings.effective_wb_sync_mode()
        try:
            result = await asyncio.wait_for(sync_wb(session), timeout=settings.wb_sync_max_runtime_seconds)
            result['source'] = source
            _sync_status['last_result'] = result
            _sync_status['last_success_at'] = _now_iso()
            return result
        except asyncio.TimeoutError as exc:
            _sync_status['last_error'] = f'Синхронизация WB остановлена по таймауту {settings.wb_sync_max_runtime_seconds} сек. Уже сохраненные блоки НЕ откатываются. Последний шаг: {_sync_status.get("progress")}'
            if isinstance(_sync_status.get('last_result'), dict):
                _sync_status['last_result']['partial_timeout'] = True
                _sync_status['last_result']['last_error'] = _sync_status['last_error']
            raise RuntimeError(_sync_status['last_error']) from exc
        except Exception as exc:
            _sync_status['last_error'] = str(exc)
            raise
        finally:
            _sync_status['running'] = False
            _sync_status['last_finished_at'] = _now_iso()
            if own_session:
                session.close()

async def wb_auto_sync_loop() -> None:
    if not settings.wb_auto_sync_enabled:
        return
    await asyncio.sleep(settings.wb_auto_sync_initial_delay_seconds)
    while True:
        try:
            await run_sync_wb_with_status(source='auto')
        except Exception as exc:
            _sync_status['last_error'] = str(exc)
        await asyncio.sleep(settings.wb_auto_sync_interval_seconds)

def get_sync_status() -> dict:
    _sync_status['auto_sync_enabled'] = settings.wb_auto_sync_enabled
    _sync_status['interval_seconds'] = settings.wb_auto_sync_interval_seconds
    _sync_status['initial_delay_seconds'] = settings.wb_auto_sync_initial_delay_seconds
    _sync_status['sync_mode'] = settings.effective_wb_sync_mode()
    _sync_status['max_runtime_seconds'] = settings.wb_sync_max_runtime_seconds
    _sync_status['request_timeout_seconds'] = settings.wb_request_timeout_seconds
    _sync_status['diagnostic_counts_enabled'] = settings.wb_diagnostic_counts_enabled
    _sync_status['feedback_count_diagnostics_enabled'] = settings.wb_feedback_count_diagnostics_enabled
    return dict(_sync_status)

# =========================
# v2.0: anti-limit block scheduler
# =========================
from datetime import timedelta
from ..ai.answer_generator import AnswerGenerator
from ..services.automation_rules import get_rules, apply_publication_rules
from ..services.publishing_service import publish_review, publish_question

WB_SYNC_BLOCKS = [
    'feedbacks_unanswered',
    'questions_unanswered',
    'feedbacks_answered',
    'questions_answered',
    'feedbacks_archive',
]

_block_state: dict[str, dict[str, Any]] = {
    name: {
        'status': 'never_run',
        'last_started_at': None,
        'last_finished_at': None,
        'last_success_at': None,
        'last_error': None,
        'next_retry_at': None,
        'last_result': None,
    } for name in WB_SYNC_BLOCKS
}
_scheduler_index = 0


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _is_rate_limit(text: str | None) -> bool:
    t = (text or '').lower()
    return '429' in t or 'too many requests' in t or 'global limiter' in t or 'rate limit' in t


def _cooldown_until() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(60, int(settings.wb_rate_limit_cooldown_seconds)))).isoformat()


def _make_client() -> WildberriesClient:
    return WildberriesClient(
        settings.wb_api_token,
        max_retries=settings.wb_retry_attempts,
        base_delay_seconds=settings.wb_retry_base_delay_seconds,
        request_pause_seconds=settings.wb_request_pause_seconds,
        request_timeout_seconds=settings.wb_request_timeout_seconds,
    )


def _enabled_blocks() -> list[str]:
    mode = settings.effective_wb_sync_mode()
    if mode == 'unanswered':
        return ['feedbacks_unanswered', 'questions_unanswered']
    if mode == 'answered':
        return ['feedbacks_answered', 'questions_answered', 'feedbacks_archive']
    return list(WB_SYNC_BLOCKS)


def _next_due_block() -> str | None:
    global _scheduler_index
    blocks = _enabled_blocks()
    now = datetime.now(timezone.utc)
    for _ in range(len(blocks)):
        block = blocks[_scheduler_index % len(blocks)]
        _scheduler_index += 1
        retry_at = _parse_dt(_block_state.get(block, {}).get('next_retry_at'))
        if retry_at and retry_at > now:
            continue
        return block
    return None


async def _maybe_autopublish(db: Session, item_type: str) -> dict[str, Any]:
    """Generate and publish only items that pass all gates.

    Real marketplace publishing requires BOTH .env ENABLE_MARKETPLACE_PUBLISHING=true
    and UI rule real_autopublish_enabled=true. Otherwise this function only reports that
    autopublish is disabled.
    """
    rules = (get_rules(db).rules or {})
    stats = {'checked': 0, 'generated': 0, 'published': 0, 'skipped': 0, 'errors': []}
    if not rules.get('auto_generate_on_sync', True):
        return {**stats, 'disabled_reason': 'Автогенерация при синхронизации выключена в Правилах ИИ.'}
    real_autopublish_allowed = bool(rules.get('real_autopublish_enabled') and settings.enable_marketplace_publishing)
    limit = max(1, min(100, int(rules.get('autopublish_max_per_run', 10))))

    # Сначала всегда готовим черновики автоматически: AI → fallback на локальный шаблон.
    # Это не зависит от реальной публикации в маркетплейс.
    from app.services.draft_generation_service import generate_missing_drafts
    draft_stats = generate_missing_drafts(db, platform='WB', item_type=item_type, limit=limit)
    stats['generated'] += int(draft_stats.get('generated') or 0)
    stats['draft_generation'] = draft_stats

    if not real_autopublish_allowed:
        stats['publish_mode'] = 'drafts_only'
        stats['disabled_reason'] = 'Черновики созданы автоматически. Реальная автопубликация выключена: нужен real_autopublish_enabled=true и ENABLE_MARKETPLACE_PUBLISHING=true.'
        db.commit()
        return stats

    generator = AnswerGenerator(rules)
    if item_type == 'review':
        rows = db.query(Review).filter(
            Review.platform == 'WB',
            Review.operational_status == 'needs_response',
            Review.source_status == 'wb_unanswered',
            Review.has_answer == False,
        ).order_by(Review.created_at_marketplace.desc()).limit(limit).all()
        for row in rows:
            stats['checked'] += 1
            try:
                result = generator.generate_for_review_until_pass({
                    'platform': row.platform, 'sku': row.sku, 'product_name': row.product_name,
                    'rating': row.rating, 'text': row.text, 'pros': row.pros, 'cons': row.cons,
                    'client_name': row.client_name,
                })
                result = apply_publication_rules(result, 'review', row.rating, db)
                row.ai_category = result.get('category')
                row.ai_sentiment = result.get('sentiment')
                row.ai_risk_level = result.get('risk_level')
                row.ai_can_autopublish = bool(result.get('can_autopublish'))
                row.ai_reason = result.get('reason')
                row.ai_tags = result.get('tags') or row.ai_tags
                row.draft_answer = result.get('answer_text') or None
                row.final_answer = result.get('answer_text') or None
                stats['generated'] += 1 if row.final_answer else 0
                if row.final_answer and row.ai_can_autopublish and real_autopublish_allowed and (rules.get('autopublish_local_templates') or 'OpenAI' not in str(row.ai_reason or '')):
                    await publish_review(db, row.id)
                    stats['published'] += 1
                else:
                    row.status = 'ready_to_review' if row.final_answer else 'answer_rejected_quality_gate'
                    stats['skipped'] += 1
                db.commit()
            except Exception as exc:
                stats['errors'].append(f'review {row.id}: {exc}')
    else:
        rows = db.query(Question).filter(
            Question.platform == 'WB',
            Question.operational_status == 'needs_response',
            Question.source_status == 'wb_unanswered',
            Question.has_answer == False,
        ).order_by(Question.created_at_marketplace.desc()).limit(limit).all()
        for row in rows:
            stats['checked'] += 1
            try:
                result = generator.generate_for_question_until_pass({
                    'platform': row.platform, 'sku': row.sku, 'product_name': row.product_name,
                    'text': row.text, 'client_name': row.client_name,
                })
                result = apply_publication_rules(result, 'question', None, db)
                row.ai_category = result.get('category')
                row.ai_risk_level = result.get('risk_level')
                row.ai_can_autopublish = bool(result.get('can_autopublish'))
                row.ai_reason = result.get('reason')
                row.ai_tags = result.get('tags') or row.ai_tags
                row.draft_answer = result.get('answer_text') or None
                row.final_answer = result.get('answer_text') or None
                stats['generated'] += 1 if row.final_answer else 0
                if row.final_answer and row.ai_can_autopublish and real_autopublish_allowed and (rules.get('autopublish_local_templates') or 'OpenAI' not in str(row.ai_reason or '')):
                    await publish_question(db, row.id)
                    stats['published'] += 1
                else:
                    row.status = 'ready_to_review' if row.final_answer else 'answer_rejected_quality_gate'
                    stats['skipped'] += 1
                db.commit()
            except Exception as exc:
                stats['errors'].append(f'question {row.id}: {exc}')
    return stats


async def sync_wb_block(db: Session, block_name: str) -> dict[str, Any]:
    if block_name not in WB_SYNC_BLOCKS:
        raise ValueError(f'Неизвестный блок синхронизации: {block_name}')

    client = _make_client()
    sync_run_id = _now_iso()
    diagnostics: dict[str, Any] = {'blocks': {}, 'warnings': [], 'block_scheduler': True}
    result: dict[str, Any] = {
        'platform': 'WB',
        'sync_mode': settings.effective_wb_sync_mode(),
        'sync_run_id': sync_run_id,
        'block': block_name,
        'imported_reviews': 0,
        'updated_reviews': 0,
        'imported_questions': 0,
        'updated_questions': 0,
        'rating_snapshots_created': 0,
        'autopublish': None,
        'diagnostics': diagnostics,
        'message': f'Блок WB {block_name} завершен',
    }

    if block_name == 'feedbacks_unanswered':
        c, u, ids = await _import_reviews_block(db, block_name, 'unanswered', lambda take, skip: client.get_feedbacks(False, take, skip), diagnostics, sync_run_id, source_status='wb_unanswered', operational_status='needs_response', has_answer_override=False)
        result['imported_reviews'], result['updated_reviews'] = c, u
        if diagnostics['blocks'][block_name]['status'] == 'success':
            diagnostics['reviews_stale_unanswered_marked'] = _mark_review_stale_unanswered(db, ids, sync_run_id)
            result['autopublish'] = await _maybe_autopublish(db, 'review')
    elif block_name == 'questions_unanswered':
        c, u, ids = await _import_questions_block(db, block_name, lambda take, skip: client.get_questions(False, take, skip), diagnostics, sync_run_id, source_status='wb_unanswered', operational_status='needs_response', has_answer_override=False)
        result['imported_questions'], result['updated_questions'] = c, u
        if diagnostics['blocks'][block_name]['status'] == 'success':
            diagnostics['questions_stale_unanswered_marked'] = _mark_question_stale_unanswered(db, ids, sync_run_id)
            result['autopublish'] = await _maybe_autopublish(db, 'question')
    elif block_name == 'feedbacks_answered':
        c, u, _ = await _import_reviews_block(db, block_name, 'answered', lambda take, skip: client.get_feedbacks(True, take, skip), diagnostics, sync_run_id, source_status='wb_answered', operational_status='analytics_only', has_answer_override=True)
        result['imported_reviews'], result['updated_reviews'] = c, u
    elif block_name == 'questions_answered':
        c, u, _ = await _import_questions_block(db, block_name, lambda take, skip: client.get_questions(True, take, skip), diagnostics, sync_run_id, source_status='wb_answered', operational_status='analytics_only', has_answer_override=True)
        result['imported_questions'], result['updated_questions'] = c, u
    elif block_name == 'feedbacks_archive':
        c, u, _ = await _import_reviews_block(db, block_name, 'archive', lambda take, skip: client.get_feedbacks_archive(take, skip), diagnostics, sync_run_id, source_status='wb_archive', operational_status='analytics_only', has_answer_override=True)
        result['imported_reviews'], result['updated_reviews'] = c, u

    block_info = diagnostics['blocks'].get(block_name, {})
    if block_info.get('status') == 'success':
        result['rating_snapshots_created'] = _snapshot_product_ratings(db)
    return result


async def run_sync_wb_block_with_status(block_name: str | None = None, db: Session | None = None, source: str = 'manual') -> dict:
    if _sync_lock.locked():
        return {'platform': 'WB', 'skipped': True, 'message': 'Синхронизация WB уже выполняется. Новый запуск пропущен.'}
    if block_name in (None, 'next'):
        block_name = _next_due_block()
        if not block_name:
            return {'platform': 'WB', 'skipped': True, 'message': 'Все блоки WB сейчас на cooldown после 429. Подожди next_retry_at в диагностике.'}

    retry_at = _parse_dt(_block_state.get(block_name, {}).get('next_retry_at'))
    now = datetime.now(timezone.utc)
    if retry_at and retry_at > now and source != 'manual_force':
        return {'platform': 'WB', 'skipped': True, 'block': block_name, 'message': f'Блок {block_name} временно на паузе до {retry_at.isoformat()} после 429.'}

    async with _sync_lock:
        own_session = db is None
        session = db or SessionLocal()
        _sync_status['running'] = True
        _sync_status['last_started_at'] = _now_iso()
        _sync_status['last_error'] = None
        _sync_status['source'] = source
        _sync_status['sync_mode'] = settings.effective_wb_sync_mode()
        _sync_status['current_block'] = block_name
        _set_progress(f'WB block scheduler: {block_name}')
        _block_state[block_name]['status'] = 'running'
        _block_state[block_name]['last_started_at'] = _now_iso()
        _block_state[block_name]['last_error'] = None
        try:
            result = await asyncio.wait_for(sync_wb_block(session, block_name), timeout=settings.wb_sync_max_runtime_seconds)
            result['source'] = source
            block_info = (result.get('diagnostics', {}).get('blocks', {}) or {}).get(block_name, {})
            status = block_info.get('status') or 'success'
            error = block_info.get('error')
            if status == 'failed' and _is_rate_limit(error):
                _block_state[block_name]['status'] = 'rate_limited'
                _block_state[block_name]['next_retry_at'] = _cooldown_until()
            elif status == 'failed':
                _block_state[block_name]['status'] = 'failed'
            else:
                _block_state[block_name]['status'] = 'success'
                _block_state[block_name]['last_success_at'] = _now_iso()
                _block_state[block_name]['next_retry_at'] = None
            _block_state[block_name]['last_result'] = result
            _block_state[block_name]['last_error'] = error
            _sync_status['last_result'] = result
            _sync_status['last_success_at'] = _now_iso() if status != 'failed' else _sync_status.get('last_success_at')
            return result
        except asyncio.TimeoutError as exc:
            msg = f'Блок WB {block_name} остановлен по таймауту {settings.wb_sync_max_runtime_seconds} сек. Последний шаг: {_sync_status.get("progress")}'
            _sync_status['last_error'] = msg
            _block_state[block_name]['status'] = 'timeout'
            _block_state[block_name]['last_error'] = msg
            _block_state[block_name]['next_retry_at'] = _cooldown_until()
            raise RuntimeError(msg) from exc
        except Exception as exc:
            msg = str(exc)
            _sync_status['last_error'] = msg
            _block_state[block_name]['status'] = 'rate_limited' if _is_rate_limit(msg) else 'failed'
            _block_state[block_name]['last_error'] = msg
            if _is_rate_limit(msg):
                _block_state[block_name]['next_retry_at'] = _cooldown_until()
            raise
        finally:
            _sync_status['running'] = False
            _sync_status['last_finished_at'] = _now_iso()
            _block_state[block_name]['last_finished_at'] = _now_iso()
            if own_session:
                session.close()


# Override old manual entrypoint: in v2.0 the main sync button runs only the next safe block.
async def run_sync_wb_with_status(db: Session | None = None, source: str = 'manual') -> dict:
    return await run_sync_wb_block_with_status('next', db=db, source=source)


async def wb_auto_sync_loop() -> None:
    if not settings.wb_auto_sync_enabled:
        return
    await asyncio.sleep(settings.wb_auto_sync_initial_delay_seconds)
    while True:
        try:
            await run_sync_wb_block_with_status('next', source='auto_block_scheduler')
        except Exception as exc:
            _sync_status['last_error'] = str(exc)
        await asyncio.sleep(settings.wb_auto_sync_interval_seconds)


def get_sync_status() -> dict:
    _sync_status['auto_sync_enabled'] = settings.wb_auto_sync_enabled
    _sync_status['auto_sync_strategy'] = settings.wb_auto_sync_strategy
    _sync_status['interval_seconds'] = settings.wb_auto_sync_interval_seconds
    _sync_status['initial_delay_seconds'] = settings.wb_auto_sync_initial_delay_seconds
    _sync_status['sync_mode'] = settings.effective_wb_sync_mode()
    _sync_status['max_runtime_seconds'] = settings.wb_sync_max_runtime_seconds
    _sync_status['request_timeout_seconds'] = settings.wb_request_timeout_seconds
    _sync_status['diagnostic_counts_enabled'] = settings.wb_diagnostic_counts_enabled
    _sync_status['feedback_count_diagnostics_enabled'] = settings.wb_feedback_count_diagnostics_enabled
    _sync_status['rate_limit_cooldown_seconds'] = settings.wb_rate_limit_cooldown_seconds
    _sync_status['blocks_state'] = _block_state
    _sync_status['enabled_blocks'] = _enabled_blocks()
    return dict(_sync_status)

# =========================
# v2.4: fully automated drip backfill
# =========================
# The operational queue is refreshed often. Historical data is backfilled automatically and slowly,
# one page at a time, so Lena does not have to remember to press manual buttons.
WB_OPERATIONAL_BLOCKS = ['feedbacks_unanswered', 'questions_unanswered']
WB_BACKFILL_BLOCKS = ['feedbacks_answered', 'questions_answered', 'feedbacks_archive']
_operational_scheduler_index = 0
_backfill_scheduler_index = 0
_all_scheduler_index = 0


def _next_due_block_from(blocks: list[str], scheduler_name: str) -> str | None:
    global _operational_scheduler_index, _backfill_scheduler_index, _all_scheduler_index
    if not blocks:
        return None
    now = datetime.now(timezone.utc)
    if scheduler_name == 'operational':
        idx = _operational_scheduler_index
    elif scheduler_name == 'backfill':
        idx = _backfill_scheduler_index
    else:
        idx = _all_scheduler_index

    for _ in range(len(blocks)):
        block = blocks[idx % len(blocks)]
        idx += 1
        retry_at = _parse_dt(_block_state.get(block, {}).get('next_retry_at'))
        if retry_at and retry_at > now:
            continue
        if scheduler_name == 'operational':
            _operational_scheduler_index = idx
        elif scheduler_name == 'backfill':
            _backfill_scheduler_index = idx
        else:
            _all_scheduler_index = idx
        return block

    if scheduler_name == 'operational':
        _operational_scheduler_index = idx
    elif scheduler_name == 'backfill':
        _backfill_scheduler_index = idx
    else:
        _all_scheduler_index = idx
    return None


async def run_sync_wb_operational_once(db: Session | None = None, source: str = 'manual_operational') -> dict:
    block = _next_due_block_from(WB_OPERATIONAL_BLOCKS, 'operational')
    if not block:
        return {'platform': 'WB', 'skipped': True, 'scheduler': 'operational', 'message': 'Операционные блоки WB сейчас на cooldown.'}
    return await run_sync_wb_block_with_status(block, db=db, source=source)


async def run_sync_wb_backfill_once(db: Session | None = None, source: str = 'manual_backfill') -> dict:
    block = _next_due_block_from(WB_BACKFILL_BLOCKS, 'backfill')
    if not block:
        return {'platform': 'WB', 'skipped': True, 'scheduler': 'backfill', 'message': 'Исторические блоки WB сейчас на cooldown.'}
    return await run_sync_wb_block_with_status(block, db=db, source=source)


async def _wb_loop(label: str, blocks: list[str], interval_seconds: int, initial_delay_seconds: int) -> None:
    await asyncio.sleep(max(0, int(initial_delay_seconds)))
    while True:
        try:
            block = _next_due_block_from(blocks, label)
            if block:
                await run_sync_wb_block_with_status(block, source=f'auto_{label}')
        except Exception as exc:
            _sync_status['last_error'] = f'auto_{label}: {exc}'
        await asyncio.sleep(max(60, int(interval_seconds)))


# Override v2.3 loop: start two independent automatic schedules.
async def wb_auto_sync_loop() -> None:
    if not settings.wb_auto_sync_enabled:
        return
    tasks = []
    if settings.wb_operational_sync_enabled:
        tasks.append(asyncio.create_task(_wb_loop(
            'operational',
            WB_OPERATIONAL_BLOCKS,
            settings.wb_operational_sync_interval_seconds,
            settings.wb_auto_sync_initial_delay_seconds,
        )))
    if settings.wb_backfill_sync_enabled:
        tasks.append(asyncio.create_task(_wb_loop(
            'backfill',
            WB_BACKFILL_BLOCKS,
            settings.wb_backfill_sync_interval_seconds,
            settings.wb_backfill_initial_delay_seconds,
        )))
    if not tasks:
        tasks.append(asyncio.create_task(_wb_loop(
            'all',
            _enabled_blocks(),
            settings.wb_auto_sync_interval_seconds,
            settings.wb_auto_sync_initial_delay_seconds,
        )))
    await asyncio.gather(*tasks)


# Override status to expose both schedules in UI/API.
def get_sync_status() -> dict:
    _sync_status['auto_sync_enabled'] = settings.wb_auto_sync_enabled
    _sync_status['auto_sync_strategy'] = settings.wb_auto_sync_strategy
    _sync_status['interval_seconds'] = settings.wb_auto_sync_interval_seconds
    _sync_status['initial_delay_seconds'] = settings.wb_auto_sync_initial_delay_seconds
    _sync_status['sync_mode'] = settings.effective_wb_sync_mode()
    _sync_status['max_runtime_seconds'] = settings.wb_sync_max_runtime_seconds
    _sync_status['request_timeout_seconds'] = settings.wb_request_timeout_seconds
    _sync_status['diagnostic_counts_enabled'] = settings.wb_diagnostic_counts_enabled
    _sync_status['feedback_count_diagnostics_enabled'] = settings.wb_feedback_count_diagnostics_enabled
    _sync_status['rate_limit_cooldown_seconds'] = settings.wb_rate_limit_cooldown_seconds
    _sync_status['blocks_state'] = _block_state
    _sync_status['enabled_blocks'] = _enabled_blocks()
    _sync_status['auto_schedules'] = {
        'operational': {
            'enabled': settings.wb_auto_sync_enabled and settings.wb_operational_sync_enabled,
            'blocks': WB_OPERATIONAL_BLOCKS,
            'interval_seconds': settings.wb_operational_sync_interval_seconds,
            'purpose': 'частая актуализация отзывов/вопросов без ответа',
        },
        'backfill': {
            'enabled': settings.wb_auto_sync_enabled and settings.wb_backfill_sync_enabled,
            'blocks': WB_BACKFILL_BLOCKS,
            'interval_seconds': settings.wb_backfill_sync_interval_seconds,
            'initial_delay_seconds': settings.wb_backfill_initial_delay_seconds,
            'pages_per_block_run': settings.wb_sync_pages_per_block_run,
            'purpose': 'автоматическая дозагрузка отвеченных отзывов, вопросов и архива по одной странице',
        },
    }
    return dict(_sync_status)

# =========================
# v3.4: automatic persistent full archive backfill override
# =========================
# This block intentionally overrides selected functions above. It makes WB archive
# backfill start automatically with the backend, keep its page cursor on disk and
# continue page-by-page until all available historical reviews/questions are loaded.
import json as _json
from pathlib import Path as _Path

_BACKFILL_STATE_FILE_V34 = _Path(__file__).resolve().parent / 'wb_backfill_state.json'


def _load_backfill_state_v34() -> dict[str, Any]:
    try:
        if _BACKFILL_STATE_FILE_V34.exists():
            data = _json.loads(_BACKFILL_STATE_FILE_V34.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_backfill_state_v34() -> None:
    try:
        payload = {}
        for name, state in _block_state.items():
            if name in HISTORICAL_BLOCKS:
                payload[name] = {
                    'next_page': state.get('next_page', 0),
                    'last_skip': state.get('last_skip'),
                    'pages_per_run': state.get('pages_per_run'),
                    'finished': bool(state.get('finished', False)),
                    'finished_at': state.get('finished_at'),
                    'last_received': state.get('last_received'),
                    'last_page': state.get('last_page'),
                    'last_success_at': state.get('last_success_at'),
                    'last_error': state.get('last_error'),
                    'status': state.get('status'),
                }
        _BACKFILL_STATE_FILE_V34.write_text(_json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        _sync_status['backfill_state_save_error'] = str(exc)


# Restore saved cursors at import time. Safe if file does not exist.
try:
    for _name, _saved in _load_backfill_state_v34().items():
        if _name in _block_state and isinstance(_saved, dict):
            _block_state[_name].update(_saved)
except Exception:
    pass


async def _fetch_pages_for_block(block_name: str, fetcher: Callable[[int, int], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    take = max(1, int(settings.wb_sync_take))
    # Very high default: the archive should keep going until WB returns an empty/partial page,
    # not stop after the old technical limit of 20 pages.
    max_pages_total = max(1, int(getattr(settings, 'wb_sync_max_pages', 100000)))
    pages_per_run = max(1, int(getattr(settings, 'wb_sync_pages_per_block_run', 1)))
    pages_per_run = min(pages_per_run, max_pages_total)

    state = _block_state.setdefault(block_name, {})
    is_historical = block_name in HISTORICAL_BLOCKS

    if is_historical:
        rescan_after = max(0, int(getattr(settings, 'wb_backfill_rescan_finished_after_seconds', 0) or 0))
        finished_at = _parse_dt(state.get('finished_at'))
        if state.get('finished') and not (rescan_after and finished_at and (datetime.now(timezone.utc) - finished_at).total_seconds() >= rescan_after):
            _set_progress(f'WB: {block_name} archive already fully loaded')
            return []
        if state.get('finished') and rescan_after:
            state['finished'] = False
            state['next_page'] = 0

    start_page = int(state.get('next_page', 0) or 0) if is_historical else 0
    if is_historical and start_page >= max_pages_total:
        state['finished'] = True
        state['finished_at'] = _now_iso()
        state['status'] = 'finished_max_pages_reached'
        _save_backfill_state_v34()
        return []

    all_items: list[dict[str, Any]] = []
    last_page_full = False
    last_page = start_page
    for i in range(pages_per_run):
        page = start_page + i
        if page >= max_pages_total:
            break
        last_page = page
        skip = page * take
        _set_progress(f'WB: {block_name} page {page + 1}/{max_pages_total}', take=take, skip=skip)
        items = await fetcher(take, skip)
        all_items.extend(items)
        last_page_full = len(items) >= take
        state['last_page'] = page
        state['last_skip'] = skip
        state['last_received'] = len(items)
        await asyncio.sleep(settings.wb_request_pause_seconds)
        if len(items) < take:
            break

    if is_historical:
        state['pages_per_run'] = pages_per_run
        if all_items and last_page_full and (last_page + 1) < max_pages_total:
            state['next_page'] = last_page + 1
            state['finished'] = False
            state['finished_at'] = None
            state['status'] = 'backfilling'
        else:
            # Empty page or partial page = end of the available archive stream.
            state['next_page'] = 0
            state['finished'] = True
            state['finished_at'] = _now_iso()
            state['status'] = 'finished'
        _save_backfill_state_v34()
    else:
        state['next_page'] = 0

    return all_items


def _next_due_block_from(blocks: list[str], scheduler_name: str) -> str | None:
    global _operational_scheduler_index, _backfill_scheduler_index, _all_scheduler_index
    if not blocks:
        return None
    now = datetime.now(timezone.utc)
    if scheduler_name == 'operational':
        idx = _operational_scheduler_index
    elif scheduler_name == 'backfill':
        idx = _backfill_scheduler_index
    else:
        idx = _all_scheduler_index

    for _ in range(len(blocks)):
        block = blocks[idx % len(blocks)]
        idx += 1
        state = _block_state.get(block, {})
        retry_at = _parse_dt(state.get('next_retry_at'))
        if retry_at and retry_at > now:
            continue
        if scheduler_name == 'backfill' and block in HISTORICAL_BLOCKS and state.get('finished'):
            rescan_after = max(0, int(getattr(settings, 'wb_backfill_rescan_finished_after_seconds', 0) or 0))
            finished_at = _parse_dt(state.get('finished_at'))
            if not (rescan_after and finished_at and (now - finished_at).total_seconds() >= rescan_after):
                continue
        if scheduler_name == 'operational':
            _operational_scheduler_index = idx
        elif scheduler_name == 'backfill':
            _backfill_scheduler_index = idx
        else:
            _all_scheduler_index = idx
        return block

    if scheduler_name == 'operational':
        _operational_scheduler_index = idx
    elif scheduler_name == 'backfill':
        _backfill_scheduler_index = idx
    else:
        _all_scheduler_index = idx
    return None


async def wb_auto_sync_loop() -> None:
    # Auto-start whenever WB token exists. Archive backfill is automatic and does not
    # require Lena to enable it manually in the interface.
    if not settings.wb_api_token:
        _sync_status['last_error'] = 'WB_API_TOKEN is empty; automatic WB sync/backfill is not started.'
        return

    tasks = []
    if settings.wb_auto_sync_enabled and settings.wb_operational_sync_enabled:
        tasks.append(asyncio.create_task(_wb_loop(
            'operational',
            WB_OPERATIONAL_BLOCKS,
            settings.wb_operational_sync_interval_seconds,
            settings.wb_auto_sync_initial_delay_seconds,
        )))

    # Archive backfill always starts automatically when WB token exists.
    if settings.wb_backfill_sync_enabled or getattr(settings, 'wb_archive_backfill_always_enabled', True):
        tasks.append(asyncio.create_task(_wb_loop(
            'backfill',
            WB_BACKFILL_BLOCKS,
            settings.wb_backfill_sync_interval_seconds,
            settings.wb_backfill_initial_delay_seconds,
        )))

    if not tasks:
        tasks.append(asyncio.create_task(_wb_loop(
            'all',
            _enabled_blocks(),
            settings.wb_auto_sync_interval_seconds,
            settings.wb_auto_sync_initial_delay_seconds,
        )))
    await asyncio.gather(*tasks)


def get_sync_status() -> dict:
    _sync_status['auto_sync_enabled'] = settings.wb_auto_sync_enabled
    _sync_status['auto_sync_strategy'] = settings.wb_auto_sync_strategy
    _sync_status['interval_seconds'] = settings.wb_auto_sync_interval_seconds
    _sync_status['initial_delay_seconds'] = settings.wb_auto_sync_initial_delay_seconds
    _sync_status['sync_mode'] = settings.effective_wb_sync_mode()
    _sync_status['max_runtime_seconds'] = settings.wb_sync_max_runtime_seconds
    _sync_status['request_timeout_seconds'] = settings.wb_request_timeout_seconds
    _sync_status['diagnostic_counts_enabled'] = settings.wb_diagnostic_counts_enabled
    _sync_status['feedback_count_diagnostics_enabled'] = settings.wb_feedback_count_diagnostics_enabled
    _sync_status['rate_limit_cooldown_seconds'] = settings.wb_rate_limit_cooldown_seconds
    _sync_status['blocks_state'] = _block_state
    _sync_status['enabled_blocks'] = _enabled_blocks()
    _sync_status['auto_schedules'] = {
        'operational': {
            'enabled': bool(settings.wb_api_token) and settings.wb_auto_sync_enabled and settings.wb_operational_sync_enabled,
            'blocks': WB_OPERATIONAL_BLOCKS,
            'interval_seconds': settings.wb_operational_sync_interval_seconds,
            'purpose': 'частая актуализация отзывов/вопросов без ответа',
        },
        'backfill': {
            'enabled': bool(settings.wb_api_token) and (settings.wb_backfill_sync_enabled or getattr(settings, 'wb_archive_backfill_always_enabled', True)),
            'blocks': WB_BACKFILL_BLOCKS,
            'interval_seconds': settings.wb_backfill_sync_interval_seconds,
            'initial_delay_seconds': settings.wb_backfill_initial_delay_seconds,
            'pages_per_block_run': settings.wb_sync_pages_per_block_run,
            'max_pages': getattr(settings, 'wb_sync_max_pages', 100000),
            'always_enabled': getattr(settings, 'wb_archive_backfill_always_enabled', True),
            'purpose': 'автоматическая перманентная дозагрузка всего доступного архива по одной странице до полного окончания',
        },
    }
    return dict(_sync_status)

# =========================
# v3.6: deterministic WB sweep scheduler
# =========================
# Previous dual-loop scheduler could look like only the first block runs: operational and
# backfill loops shared one global lock. This override uses one deterministic sweep:
# every cycle attempts all WB blocks in order. A 429 on one block only puts that block
# on cooldown and the sweep continues to the next block.

async def _run_wb_block_safely_v36(block_name: str, source: str) -> dict[str, Any]:
    retry_at = _parse_dt(_block_state.get(block_name, {}).get('next_retry_at'))
    now = datetime.now(timezone.utc)
    if retry_at and retry_at > now:
        return {'platform': 'WB', 'skipped': True, 'block': block_name, 'reason': 'cooldown', 'next_retry_at': retry_at.isoformat()}
    try:
        return await run_sync_wb_block_with_status(block_name, source=source)
    except Exception as exc:
        return {'platform': 'WB', 'block': block_name, 'failed': True, 'error': str(exc)}


def _wb_sweep_blocks_v36() -> list[str]:
    blocks: list[str] = []
    if settings.wb_operational_sync_enabled:
        blocks.extend(WB_OPERATIONAL_BLOCKS)
    if settings.wb_backfill_sync_enabled or getattr(settings, 'wb_archive_backfill_always_enabled', True):
        blocks.extend(WB_BACKFILL_BLOCKS)
    if not blocks:
        blocks = _enabled_blocks()
    seen = set()
    unique = []
    for block in blocks:
        if block not in seen:
            seen.add(block)
            unique.append(block)
    return unique


async def wb_auto_sync_loop() -> None:
    if not settings.wb_api_token:
        _sync_status['last_error'] = 'WB_API_TOKEN is empty; automatic WB sync/backfill is not started.'
        return
    if not settings.wb_auto_sync_enabled:
        _sync_status['auto_sync_enabled'] = False
        _sync_status['last_error'] = 'WB auto sync is disabled by WB_AUTO_SYNC_ENABLED=false.'
        return

    await asyncio.sleep(max(0, int(settings.wb_auto_sync_initial_delay_seconds)))

    while True:
        blocks = _wb_sweep_blocks_v36()
        _sync_status['scheduler_mode'] = 'v3.6_sweep_all_blocks'
        _sync_status['sweep_blocks'] = blocks
        _sync_status['sweep_started_at'] = _now_iso()
        _sync_status['sweep_results'] = []
        _sync_status['last_error'] = None

        for block in blocks:
            result = await _run_wb_block_safely_v36(block, source='auto_sweep_v36')
            _sync_status.setdefault('sweep_results', []).append(result)
            await asyncio.sleep(max(1, int(settings.wb_request_pause_seconds)))

        _sync_status['sweep_finished_at'] = _now_iso()
        interval = max(60, int(settings.wb_auto_sync_interval_seconds))
        _sync_status['next_sweep_after_seconds'] = interval
        await asyncio.sleep(interval)


def get_sync_status() -> dict:
    _sync_status['auto_sync_enabled'] = settings.wb_auto_sync_enabled
    _sync_status['auto_sync_strategy'] = 'v3.6_sweep_all_blocks'
    _sync_status['interval_seconds'] = settings.wb_auto_sync_interval_seconds
    _sync_status['initial_delay_seconds'] = settings.wb_auto_sync_initial_delay_seconds
    _sync_status['sync_mode'] = settings.effective_wb_sync_mode()
    _sync_status['max_runtime_seconds'] = settings.wb_sync_max_runtime_seconds
    _sync_status['request_timeout_seconds'] = settings.wb_request_timeout_seconds
    _sync_status['diagnostic_counts_enabled'] = settings.wb_diagnostic_counts_enabled
    _sync_status['feedback_count_diagnostics_enabled'] = settings.wb_feedback_count_diagnostics_enabled
    _sync_status['rate_limit_cooldown_seconds'] = settings.wb_rate_limit_cooldown_seconds
    _sync_status['blocks_state'] = _block_state
    _sync_status['enabled_blocks'] = _enabled_blocks()
    _sync_status['sweep_blocks'] = _wb_sweep_blocks_v36()
    _sync_status['auto_schedules'] = {
        'sweep': {
            'enabled': bool(settings.wb_api_token) and settings.wb_auto_sync_enabled,
            'blocks': _wb_sweep_blocks_v36(),
            'interval_seconds': settings.wb_auto_sync_interval_seconds,
            'initial_delay_seconds': settings.wb_auto_sync_initial_delay_seconds,
            'purpose': 'каждый цикл последовательно запускает все блоки WB; 429 одного блока не блокирует остальные',
        },
        'backfill': {
            'enabled': bool(settings.wb_api_token) and (settings.wb_backfill_sync_enabled or getattr(settings, 'wb_archive_backfill_always_enabled', True)),
            'blocks': WB_BACKFILL_BLOCKS,
            'pages_per_block_run': settings.wb_sync_pages_per_block_run,
            'max_pages': getattr(settings, 'wb_sync_max_pages', 100000),
            'purpose': 'перманентная дозагрузка архива по страницам до конца доступных данных',
        },
    }
    return dict(_sync_status)

# =========================
# RC1: WB Recovery hardening
# =========================
# The product must sync all WB blocks regardless of the old WB_SYNC_MODE value.
# UI filters decide what is operational debt; sync must keep the full local mirror.
def _enabled_blocks() -> list[str]:
    return list(WB_SYNC_BLOCKS)


def _wb_sweep_blocks_v36() -> list[str]:
    return list(WB_SYNC_BLOCKS)


async def wb_questions_probe(db: Session | None = None) -> dict[str, Any]:
    """Direct diagnostic probe for WB questions without touching UI assumptions."""
    client = _make_client()
    out: dict[str, Any] = {"platform": "WB", "probe": "questions", "blocks": {}}
    for answered in [False, True]:
        label = "questions_answered" if answered else "questions_unanswered"
        try:
            items = await client.get_questions(is_answered=answered, take=max(1, int(settings.wb_sync_take)), skip=0)
            out["blocks"][label] = {
                "status": "success",
                "received": len(items),
                "sample_keys": sorted(list(items[0].keys())) if items else [],
                "sample_id": str(items[0].get("id") or items[0].get("questionId") or "") if items else None,
            }
        except Exception as exc:
            out["blocks"][label] = {"status": "failed", "error": str(exc), "received": 0}
        await asyncio.sleep(max(1, int(settings.wb_request_pause_seconds)))
    try:
        out["count_unanswered"] = await client.get_questions_unanswered_count()
    except Exception as exc:
        out["count_unanswered"] = {"error": str(exc)}
    try:
        out["count_total"] = await client.get_questions_count()
    except Exception as exc:
        out["count_total"] = {"error": str(exc)}
    return out


def get_sync_status() -> dict:
    _sync_status['auto_sync_enabled'] = settings.wb_auto_sync_enabled
    _sync_status['auto_sync_strategy'] = 'rc1_wb_recovery_sweep_all_blocks'
    _sync_status['interval_seconds'] = settings.wb_auto_sync_interval_seconds
    _sync_status['initial_delay_seconds'] = settings.wb_auto_sync_initial_delay_seconds
    _sync_status['sync_mode'] = 'all_blocks_for_local_mirror'
    _sync_status['max_runtime_seconds'] = settings.wb_sync_max_runtime_seconds
    _sync_status['request_timeout_seconds'] = settings.wb_request_timeout_seconds
    _sync_status['diagnostic_counts_enabled'] = settings.wb_diagnostic_counts_enabled
    _sync_status['feedback_count_diagnostics_enabled'] = settings.wb_feedback_count_diagnostics_enabled
    _sync_status['rate_limit_cooldown_seconds'] = settings.wb_rate_limit_cooldown_seconds
    _sync_status['blocks_state'] = _block_state
    _sync_status['enabled_blocks'] = list(WB_SYNC_BLOCKS)
    _sync_status['sweep_blocks'] = list(WB_SYNC_BLOCKS)
    _sync_status['auto_schedules'] = {
        'sweep': {
            'enabled': bool(settings.wb_api_token) and settings.wb_auto_sync_enabled,
            'blocks': list(WB_SYNC_BLOCKS),
            'interval_seconds': settings.wb_auto_sync_interval_seconds,
            'initial_delay_seconds': settings.wb_auto_sync_initial_delay_seconds,
            'purpose': 'RC1: зеркалим все отзывы и вопросы WB: свежие, отвеченные и архивные; режим WB_SYNC_MODE больше не обрезает локальное зеркало',
        },
        'backfill': {
            'enabled': bool(settings.wb_api_token) and (settings.wb_backfill_sync_enabled or getattr(settings, 'wb_archive_backfill_always_enabled', True)),
            'blocks': WB_BACKFILL_BLOCKS,
            'pages_per_block_run': settings.wb_sync_pages_per_block_run,
            'max_pages': getattr(settings, 'wb_sync_max_pages', 100000),
            'purpose': 'перманентная дозагрузка архива и отвеченных вопросов/отзывов по страницам до конца доступных данных',
        },
    }
    _sync_status['health_hint'] = {
        'wb_questions_zero': (_block_state.get('questions_unanswered', {}).get('last_received') in (None, 0) and _block_state.get('questions_answered', {}).get('last_received') in (None, 0)),
        'next_action': 'Если WB questions остаются 0, вызови /marketplace-health/wb/questions-probe — он покажет, что реально возвращает WB Questions API.'
    }
    return dict(_sync_status)
