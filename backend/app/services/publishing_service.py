from __future__ import annotations
from sqlalchemy.orm import Session
from ..config import settings
from ..models import Review, Question
from ..marketplace_clients.wb import WildberriesClient
from ..marketplace_clients.ozon import OzonClient

async def publish_review(db: Session, review_id: int, response_origin: str = 'manual_app') -> dict:
    review = db.get(Review, review_id)
    if not review:
        raise ValueError('Отзыв не найден')
    if not review.final_answer:
        raise ValueError('Нет финального ответа')
    if review.operational_status != 'needs_response' or review.source_status not in {'wb_unanswered', 'ozon_unanswered'}:
        raise ValueError(review.publish_blocked_reason or 'Отзыв не находится в актуальной очереди площадки “без ответа”. Публикация заблокирована.')
    if review.platform not in {'WB', 'OZON'}:
        raise ValueError('Публикация подключена только для WB и Ozon')
    if not settings.enable_marketplace_publishing:
        review.status = 'publish_dry_run'
        db.commit()
        return {'status': 'dry_run', 'message': 'Dry-run выполнен: ответ НЕ отправлен в WB, потому что ENABLE_MARKETPLACE_PUBLISHING=false. Чтобы публиковать реально, поставь true в .env и перезапусти приложение.'}
    if review.platform == 'WB':
        client = WildberriesClient(settings.wb_api_token, max_retries=settings.wb_retry_attempts, base_delay_seconds=settings.wb_retry_base_delay_seconds, request_pause_seconds=settings.wb_request_pause_seconds, request_timeout_seconds=settings.wb_request_timeout_seconds)
        await client.answer_feedback(review.external_id, review.final_answer)
        review.source_status = 'wb_answered'
        message = 'Ответ опубликован в WB'
    else:
        client = OzonClient(settings.ozon_client_id, settings.ozon_api_key, request_timeout_seconds=settings.ozon_request_timeout_seconds, request_pause_seconds=settings.ozon_request_pause_seconds)
        await client.publish_review_answer(review.external_id, review.final_answer)
        review.source_status = 'ozon_answered'
        message = 'Ответ опубликован в Ozon'
    review.status = 'published'
    review.has_answer = True
    review.response_origin = response_origin
    review.operational_status = 'analytics_only'
    review.publish_blocked_reason = 'Ответ уже опубликован через приложение.'
    db.commit()
    return {'status': 'published', 'message': message}

async def publish_question(db: Session, question_id: int, response_origin: str = 'manual_app') -> dict:
    question = db.get(Question, question_id)
    if not question:
        raise ValueError('Вопрос не найден')
    if not question.final_answer:
        raise ValueError('Нет финального ответа')
    if question.operational_status != 'needs_response' or question.source_status not in {'wb_unanswered', 'ozon_unanswered'}:
        raise ValueError(question.publish_blocked_reason or 'Вопрос не находится в актуальной очереди площадки “без ответа”. Публикация заблокирована.')
    if question.platform not in {'WB', 'OZON'}:
        raise ValueError('Публикация подключена только для WB и Ozon')
    if not settings.enable_marketplace_publishing:
        question.status = 'publish_dry_run'
        db.commit()
        return {'status': 'dry_run', 'message': 'Dry-run выполнен: ответ НЕ отправлен в WB, потому что ENABLE_MARKETPLACE_PUBLISHING=false. Чтобы публиковать реально, поставь true в .env и перезапусти приложение.'}
    if question.platform == 'WB':
        client = WildberriesClient(settings.wb_api_token, max_retries=settings.wb_retry_attempts, base_delay_seconds=settings.wb_retry_base_delay_seconds, request_pause_seconds=settings.wb_request_pause_seconds, request_timeout_seconds=settings.wb_request_timeout_seconds)
        await client.answer_question(question.external_id, question.final_answer)
        question.source_status = 'wb_answered'
        message = 'Ответ опубликован в WB'
    else:
        client = OzonClient(settings.ozon_client_id, settings.ozon_api_key, request_timeout_seconds=settings.ozon_request_timeout_seconds, request_pause_seconds=settings.ozon_request_pause_seconds)
        await client.publish_question_answer(question.external_id, question.final_answer)
        question.source_status = 'ozon_answered'
        message = 'Ответ опубликован в Ozon'
    question.status = 'published'
    question.has_answer = True
    question.response_origin = response_origin
    question.operational_status = 'analytics_only'
    question.publish_blocked_reason = 'Ответ уже опубликован через приложение.'
    db.commit()
    return {'status': 'published', 'message': message}

async def edit_published_review_answer(db: Session, review_id: int) -> dict:
    review = db.get(Review, review_id)
    if not review:
        raise ValueError('Отзыв не найден')
    if review.platform != 'WB':
        raise ValueError('Редактирование ответа подключено только для WB')
    if not review.final_answer:
        raise ValueError('Нет текста ответа для редактирования')
    if not review.has_answer and review.source_status not in {'wb_answered', 'wb_archive'} and review.status != 'published':
        raise ValueError('WB не считает этот отзыв отвеченным. Используй обычную публикацию, а не редактирование.')
    if not settings.enable_marketplace_publishing:
        review.status = 'edit_dry_run'
        db.commit()
        return {'status': 'dry_run', 'message': 'Dry-run редактирования выполнен: ответ НЕ изменен в WB, потому что ENABLE_MARKETPLACE_PUBLISHING=false.'}
    client = WildberriesClient(settings.wb_api_token, max_retries=settings.wb_retry_attempts, base_delay_seconds=settings.wb_retry_base_delay_seconds, request_pause_seconds=settings.wb_request_pause_seconds, request_timeout_seconds=settings.wb_request_timeout_seconds)
    await client.edit_feedback_answer(review.external_id, review.final_answer)
    review.status = 'answer_edited_in_wb'
    review.response_origin = 'manual_app'
    review.has_answer = True
    review.source_status = 'wb_answered'
    review.operational_status = 'analytics_only'
    review.publish_blocked_reason = 'Ответ был отредактирован через приложение. Повторное редактирование может быть ограничено правилами WB.'
    db.commit()
    return {'status': 'edited', 'message': 'Ответ на отзыв отредактирован в WB'}


async def publish_reviews_bulk(db: Session, review_ids: list[int]) -> dict:
    results = []
    ok = 0
    failed = 0
    for review_id in review_ids:
        try:
            result = await publish_review(db, int(review_id))
            ok += 1
            results.append({'id': review_id, 'ok': True, 'result': result})
        except Exception as exc:
            failed += 1
            results.append({'id': review_id, 'ok': False, 'error': str(exc)})
    return {'status': 'done', 'requested': len(review_ids), 'published': ok, 'failed': failed, 'results': results}

async def publish_questions_bulk(db: Session, question_ids: list[int]) -> dict:
    results = []
    ok = 0
    failed = 0
    for question_id in question_ids:
        try:
            result = await publish_question(db, int(question_id))
            ok += 1
            results.append({'id': question_id, 'ok': True, 'result': result})
        except Exception as exc:
            failed += 1
            results.append({'id': question_id, 'ok': False, 'error': str(exc)})
    return {'status': 'done', 'requested': len(question_ids), 'published': ok, 'failed': failed, 'results': results}
