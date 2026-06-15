import random
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
from ..database import get_db
from ..models import Question
from ..schemas import QuestionOut, AnswerUpdate
from ..ai.answer_generator import AnswerGenerator
from ..services.automation_rules import apply_publication_rules, get_rules
from ..services.publishing_service import publish_question, publish_questions_bulk

router = APIRouter(prefix='/questions', tags=['questions'])

@router.get('', response_model=list[QuestionOut])
def list_questions(status: str | None = None, platform: str | None = None, answer_state: str = 'all', source_status: str | None = None, product: str | None = None, category: str | None = None, risk: str | None = None, response_origin: str | None = None, limit: int = 500, db: Session = Depends(get_db)):
    q = db.query(Question)
    if status:
        q = q.filter(Question.status == status)
    if platform:
        q = q.filter(Question.platform == platform)
    if source_status:
        q = q.filter(Question.source_status == source_status)
    if product:
        like = f'%{product}%'
        q = q.filter(or_(Question.sku == product, Question.product_name.ilike(like), Question.external_id == product))
    if category:
        q = q.filter(Question.ai_category == category)
    if risk:
        q = q.filter(Question.ai_risk_level == risk)
    if response_origin:
        q = q.filter(Question.response_origin == response_origin)
    if answer_state == 'answered':
        q = q.filter(Question.source_status.in_(['wb_answered', 'ozon_answered']))
    elif answer_state == 'unanswered':
        q = q.filter(Question.operational_status == 'needs_response', Question.source_status.in_(['wb_unanswered', 'ozon_unanswered']))
    elif answer_state == 'stale':
        q = q.filter(Question.operational_status == 'stale_unanswered')
    elif answer_state == 'manual':
        q = q.filter(Question.status.in_(['ready_to_review','ready_to_publish','answer_rejected_quality_gate']))
    elif answer_state == 'auto_published':
        q = q.filter(Question.status.in_(['auto_published','published']))
    return q.order_by(desc(Question.created_at_marketplace), desc(Question.created_at)).limit(limit).all()

@router.post('/{question_id}/generate', response_model=QuestionOut)
def generate_question_answer(question_id: int, db: Session = Depends(get_db)):
    question = db.get(Question, question_id)
    if not question:
        raise HTTPException(404, 'Вопрос не найден')
    result = AnswerGenerator(get_rules(db).rules).generate_for_question_until_pass({
        'platform': question.platform, 'sku': question.sku, 'product_name': question.product_name,
        'text': question.text, 'client_name': question.client_name, 'variation_seed': random.randint(1, 1_000_000)
    })
    result = apply_publication_rules(result, 'question', None, db)
    question.ai_category = result.get('category')
    question.ai_risk_level = result.get('risk_level')
    question.ai_can_autopublish = bool(result.get('can_autopublish'))
    question.ai_reason = result.get('reason')
    question.ai_tags = result.get('tags') or question.ai_tags
    question.draft_answer = result.get('answer_text') or None
    question.final_answer = result.get('answer_text') or None
    # Ответ показываем только если quality gate дал 10/10. Иначе оставляем пусто и просим переписать.
    if not result.get('answer_text'):
        question.status = 'answer_rejected_quality_gate'
    else:
        question.status = 'ready_to_review' if question.operational_status == 'needs_response' else 'local_draft'
    db.commit()
    db.refresh(question)
    return question

@router.patch('/{question_id}/answer', response_model=QuestionOut)
def update_question_answer(question_id: int, payload: AnswerUpdate, db: Session = Depends(get_db)):
    question = db.get(Question, question_id)
    if not question:
        raise HTTPException(404, 'Вопрос не найден')
    question.final_answer = payload.final_answer
    question.status = 'ready_to_publish' if question.operational_status == 'needs_response' else 'local_edited'
    db.commit()
    db.refresh(question)
    return question


@router.post('/bulk-publish')
async def bulk_publish(payload: dict, db: Session = Depends(get_db)):
    ids = payload.get('ids') or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, 'Передай список ids для публикации')
    try:
        return await publish_questions_bulk(db, ids)
    except Exception as exc:
        raise HTTPException(400, str(exc))

@router.post('/{question_id}/publish')
async def publish(question_id: int, db: Session = Depends(get_db)):
    try:
        return await publish_question(db, question_id)
    except Exception as exc:
        raise HTTPException(400, str(exc))
