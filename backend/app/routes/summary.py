from __future__ import annotations
from sqlalchemy.orm import Session
from sqlalchemy import func, case, desc, or_
from fastapi import APIRouter, Depends, Query
from ..database import get_db
from ..models import Review, Question

router = APIRouter(prefix='/summary', tags=['summary'])

def _norm_platform(platform: str | None) -> str | None:
    if not platform or platform.lower() == 'all':
        return None
    return platform.upper()

def _review_query(db: Session, platform: str | None = None, product: str | None = None):
    q = db.query(Review)
    p = _norm_platform(platform)
    if p:
        q = q.filter(Review.platform == p)
    if product:
        like = f'%{product}%'
        q = q.filter(or_(Review.sku == product, Review.product_name.ilike(like), Review.external_id == product))
    return q

def _question_query(db: Session, platform: str | None = None, product: str | None = None):
    q = db.query(Question)
    p = _norm_platform(platform)
    if p:
        q = q.filter(Question.platform == p)
    if product:
        like = f'%{product}%'
        q = q.filter(or_(Question.sku == product, Question.product_name.ilike(like), Question.external_id == product))
    return q

def _pairs(rows, empty='не классифицировано'):
    return [{'name': k or empty, 'count': int(v or 0)} for k, v in rows]

def _safe_text(value: str | None, max_len: int = 220) -> str:
    text = ' '.join((value or '').split())
    return text[:max_len].rstrip() + '…' if len(text) > max_len else text

def _examples(query, limit=3):
    rows = query.order_by(desc(Review.created_at_marketplace), desc(Review.created_at)).limit(limit).all()
    return [{
        'id': r.id, 'platform': r.platform, 'sku': r.sku, 'product_name': r.product_name,
        'product_url': r.product_url, 'rating': r.rating,
        'text': _safe_text(r.text or r.pros or r.cons or 'Без текста'),
        'category': r.ai_category, 'sentiment': r.ai_sentiment, 'tags': r.ai_tags or [],
    } for r in rows]

def _category_details(db: Session, platform: str | None = None, product: str | None = None):
    base = _review_query(db, platform, product)
    rows = base.with_entities(Review.ai_category, func.count(Review.id)).group_by(Review.ai_category).order_by(func.count(Review.id).desc()).limit(20).all()
    details = []
    for category, count in rows:
        category_label = category or 'не классифицировано'
        q = _review_query(db, platform, product).filter(Review.ai_category == category)
        product_rows = _review_query(db, platform, product).with_entities(Review.platform, Review.sku, Review.product_name, func.count(Review.id).label('cnt')).filter(Review.ai_category == category).group_by(Review.platform, Review.sku, Review.product_name).order_by(desc('cnt')).limit(8).all()
        top_products = []
        for p, sku, name, cnt in product_rows:
            sample = db.query(Review).filter(Review.platform == p, Review.sku == sku, Review.product_name == name).first()
            top_products.append({'platform': p, 'sku': sku, 'product_name': name, 'product_url': sample.product_url if sample else None, 'count': int(cnt or 0)})
        details.append({'category': category_label, 'count': int(count or 0), 'examples': _examples(q, 4), 'top_products': top_products})
    return details

def _textual_insights(db: Session, total_reviews: int, negative_reviews: int, platform: str | None = None, product: str | None = None):
    base = _review_query(db, platform, product)
    praise_examples = _examples(_review_query(db, platform, product).filter(Review.ai_sentiment == 'positive'), 5)
    complaint_examples = _examples(_review_query(db, platform, product).filter((Review.ai_sentiment == 'negative') | (Review.rating <= 3)), 5)
    top_complaints = base.with_entities(Review.ai_category, func.count(Review.id)).filter((Review.ai_sentiment == 'negative') | (Review.rating <= 3)).group_by(Review.ai_category).order_by(func.count(Review.id).desc()).limit(10).all()
    top_praise = _review_query(db, platform, product).with_entities(Review.ai_category, func.count(Review.id)).filter(Review.ai_sentiment == 'positive').group_by(Review.ai_category).order_by(func.count(Review.id).desc()).limit(10).all()
    complaint_line = ', '.join([f'{c or "не классифицировано"} — {n}' for c, n in top_complaints]) or 'нет выраженных жалоб в загруженной базе'
    praise_line = ', '.join([f'{c or "позитив"} — {n}' for c, n in top_praise]) or 'нет достаточного объема позитивных отзывов'
    share = round((negative_reviews / total_reviews * 100), 1) if total_reviews else 0
    p = _norm_platform(platform) or 'всем площадкам'
    text = (f'CX-сводка по {p}. Загружено отзывов: {total_reviews}. Негативных или низких оценок: {negative_reviews} ({share}%).\n'
            f'За что чаще хвалят: {praise_line}.\n'
            f'На что чаще жалуются: {complaint_line}.\n'
            'Для детализации нажми на категорию ниже: в карточке категории есть примеры отзывов и товары, где эта тема встречается чаще всего.')
    return {'text': text, 'praise_examples': praise_examples, 'complaint_examples': complaint_examples,
            'top_complaints': [{'name': c or 'не классифицировано', 'count': int(n or 0)} for c, n in top_complaints],
            'top_praise': [{'name': c or 'позитив', 'count': int(n or 0)} for c, n in top_praise]}



def _recommendations(db: Session, platform: str | None = None, product: str | None = None):
    base = _review_query(db, platform, product)
    product_rows = base.with_entities(
        Review.platform, Review.sku, Review.product_name,
        func.count(Review.id).label('total'),
        func.sum(case((Review.rating <= 3, 1), else_=0)).label('negative'),
        func.avg(Review.rating).label('avg_rating')
    ).group_by(Review.platform, Review.sku, Review.product_name).order_by(desc('negative'), desc('total')).limit(12).all()
    recs = []
    for platform_value, sku, product_name, total, negative, avg_rating in product_rows:
        total = int(total or 0); negative = int(negative or 0)
        if total == 0:
            continue
        neg_share = negative / total
        cats = _review_query(db, platform_value, sku).with_entities(Review.ai_category, func.count(Review.id)).filter((Review.rating <= 3) | (Review.ai_sentiment == 'negative')).group_by(Review.ai_category).order_by(func.count(Review.id).desc()).limit(3).all()
        top_cats = [c or 'не классифицировано' for c, _ in cats]
        if negative >= 2 or neg_share >= 0.25 or (avg_rating is not None and float(avg_rating) <= 4.0):
            action = 'передать технологу/производству на разбор конструкции и качества' if any(c in {'замок/застежка','камень/вставка','качество','качество/брак','проба/маркировка'} for c in top_cats) else 'разобрать причины негатива и обновить карточку товара/шаблон ответа'
            sample = db.query(Review).filter(Review.platform == platform_value, Review.sku == sku).first()
            recs.append({'platform': platform_value, 'sku': sku, 'product_name': product_name, 'product_url': sample.product_url if sample else None, 'total': total, 'negative': negative, 'negative_share': round(neg_share * 100, 1), 'rating_avg': round(float(avg_rating), 2) if avg_rating is not None else None, 'problem_categories': top_cats, 'recommendation': action})
    category_rows = base.with_entities(Review.ai_category, func.count(Review.id).label('cnt')).filter((Review.rating <= 3) | (Review.ai_sentiment == 'negative')).group_by(Review.ai_category).order_by(desc('cnt')).limit(8).all()
    group_recs = []
    for cat, cnt in category_rows:
        cat = cat or 'не классифицировано'
        action = 'собрать примеры и передать в производство/технологам' if cat in {'замок/застежка','камень/вставка','качество','качество/брак','проба/маркировка'} else 'проверить карточки, ожидания клиента и шаблоны ответов'
        group_recs.append({'category': cat, 'count': int(cnt or 0), 'recommendation': action})
    return {'products': recs, 'groups': group_recs}

@router.get('')
def summary(platform: str | None = Query(None), product: str | None = Query(None), db: Session = Depends(get_db)):
    p = _norm_platform(platform)
    rq = _review_query(db, p, product)
    qq = _question_query(db, p, product)
    total_reviews = rq.with_entities(func.count(Review.id)).scalar() or 0
    total_questions = qq.with_entities(func.count(Question.id)).scalar() or 0
    unanswered_reviews = rq.filter(Review.operational_status == 'needs_response', Review.source_status.in_(['wb_unanswered','ozon_unanswered'])).with_entities(func.count(Review.id)).scalar() or 0
    unanswered_questions = qq.filter(Question.operational_status == 'needs_response', Question.source_status.in_(['wb_unanswered','ozon_unanswered'])).with_entities(func.count(Question.id)).scalar() or 0
    negative_reviews = rq.filter(Review.rating <= 3).with_entities(func.count(Review.id)).scalar() or 0
    review_categories = rq.with_entities(Review.ai_category, func.count(Review.id)).group_by(Review.ai_category).order_by(func.count(Review.id).desc()).all()
    review_sentiments = rq.with_entities(Review.ai_sentiment, func.count(Review.id)).group_by(Review.ai_sentiment).order_by(func.count(Review.id).desc()).all()
    review_risks = rq.with_entities(Review.ai_risk_level, func.count(Review.id)).group_by(Review.ai_risk_level).order_by(func.count(Review.id).desc()).all()
    question_categories = qq.with_entities(Question.ai_category, func.count(Question.id)).group_by(Question.ai_category).order_by(func.count(Question.id).desc()).all()
    question_risks = qq.with_entities(Question.ai_risk_level, func.count(Question.id)).group_by(Question.ai_risk_level).order_by(func.count(Question.id).desc()).all()
    by_sku_negative = rq.with_entities(Review.platform, Review.sku, Review.product_name, func.count(Review.id).label('total'), func.sum(case((Review.rating <= 3, 1), else_=0)).label('negative')).group_by(Review.platform, Review.sku, Review.product_name).order_by(func.sum(case((Review.rating <= 3, 1), else_=0)).desc()).limit(20).all()
    sku_negative = []
    for platform_value, sku, product_name, total, negative in by_sku_negative:
        sample = db.query(Review).filter(Review.platform == platform_value, Review.sku == sku, Review.product_name == product_name).first()
        sku_negative.append({'platform': platform_value, 'sku': sku or '—', 'product_name': product_name, 'product_url': sample.product_url if sample else None, 'total': int(total or 0), 'negative': int(negative or 0)})
    answered_reviews = rq.filter(Review.source_status.in_(['wb_answered', 'wb_archive', 'ozon_answered'])).with_entities(func.count(Review.id)).scalar() or 0
    answered_questions = qq.filter(Question.source_status.in_(['wb_answered','ozon_answered'])).with_entities(func.count(Question.id)).scalar() or 0
    return {
        'platform': p or 'ALL', 'total_reviews': total_reviews, 'total_questions': total_questions,
        'unanswered_reviews': unanswered_reviews, 'unanswered_questions': unanswered_questions,
        'answered_reviews': answered_reviews, 'answered_questions': answered_questions,
        'negative_reviews': negative_reviews,
        'stale_unanswered_reviews': rq.filter(Review.operational_status == 'stale_unanswered').with_entities(func.count(Review.id)).scalar() or 0,
        'stale_unanswered_questions': qq.filter(Question.operational_status == 'stale_unanswered').with_entities(func.count(Question.id)).scalar() or 0,
        'review_categories': _pairs(review_categories), 'review_sentiments': _pairs(review_sentiments, 'не определено'),
        'review_risks': _pairs(review_risks, 'не определено'), 'question_categories': _pairs(question_categories),
        'question_risks': _pairs(question_risks, 'не определено'),
        'textual_insights': _textual_insights(db, total_reviews, negative_reviews, p, product),
        'category_details': _category_details(db, p, product),
        'recommendations': _recommendations(db, p, product),
        'categories': [{'category': x['name'], 'count': x['count']} for x in _pairs(review_categories)],
        'sku_negative': sku_negative,
    }
