from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_
from ..database import get_db
from ..models import Review, Question, RatingSnapshot

router = APIRouter(prefix='/analytics', tags=['analytics'])

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

def _product_key(review: Review) -> str:
    return f"{review.platform}:{review.sku or review.product_name or review.external_id}"

def _best_product_url(items: list[Review]) -> str | None:
    for r in items:
        if r.product_url:
            return r.product_url
    return None


def _avg_rating_for_window(items: list[Review], start: datetime, end: datetime):
    ratings = []
    for r in items:
        dt = r.created_at_marketplace or r.created_at
        if dt and start <= dt < end and r.rating is not None:
            ratings.append(r.rating)
    return sum(ratings) / len(ratings) if ratings else None

def _delta(a, b):
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 2)

def products_from_reviews(db: Session, platform: str | None = None, product: str | None = None):
    reviews = _review_query(db, platform, product).all()
    groups: dict[str, list[Review]] = defaultdict(list)
    for r in reviews:
        groups[_product_key(r)].append(r)
    result = []
    for key, items in groups.items():
        ratings = [r.rating for r in items if r.rating is not None]
        avg = sum(ratings) / len(ratings) if ratings else None
        negative = sum(1 for r in items if r.rating is not None and r.rating <= 3)
        positive = sum(1 for r in items if r.rating is not None and r.rating >= 4)
        categories = defaultdict(int)
        sentiments = defaultdict(int)
        for r in items:
            categories[r.ai_category or 'не классифицировано'] += 1
            sentiments[r.ai_sentiment or 'не определено'] += 1
        latest = max((r.created_at_marketplace or r.created_at for r in items), default=None)
        now = datetime.utcnow()
        day_now = _avg_rating_for_window(items, now - timedelta(days=1), now)
        day_prev = _avg_rating_for_window(items, now - timedelta(days=2), now - timedelta(days=1))
        week_now = _avg_rating_for_window(items, now - timedelta(days=7), now)
        week_prev = _avg_rating_for_window(items, now - timedelta(days=14), now - timedelta(days=7))
        month_now = _avg_rating_for_window(items, now - timedelta(days=30), now)
        month_prev = _avg_rating_for_window(items, now - timedelta(days=60), now - timedelta(days=30))
        result.append({
            'platform': items[0].platform if items else None,
            'product_key': key,
            'sku': next((r.sku for r in items if r.sku), None),
            'product_name': next((r.product_name for r in items if r.product_name), None),
            'product_url': _best_product_url(items),
            'reviews_count': len(items),
            'rating_avg': round(avg, 2) if avg is not None else None,
            'rating_delta_day': _delta(day_now, day_prev),
            'rating_delta_week': _delta(week_now, week_prev),
            'rating_delta_month': _delta(month_now, month_prev),
            'negative_count': negative,
            'positive_count': positive,
            'latest_review_at': latest.isoformat() if latest else None,
            'top_categories': sorted([{'name': k, 'count': v} for k, v in categories.items()], key=lambda x: x['count'], reverse=True)[:5],
            'sentiments': sorted([{'name': k, 'count': v} for k, v in sentiments.items()], key=lambda x: x['count'], reverse=True),
        })
    result.sort(key=lambda x: (x['negative_count'], x['reviews_count']), reverse=True)
    return result

@router.get('/products')
def products(platform: str | None = Query(None), product: str | None = Query(None), db: Session = Depends(get_db)):
    return products_from_reviews(db, platform, product)

@router.get('/anomalies')
def anomalies(platform: str | None = Query(None), product: str | None = Query(None), db: Session = Depends(get_db)):
    out = []
    for p in products_from_reviews(db, platform, product):
        score = 0
        reasons = []
        if p['rating_avg'] is not None and p['rating_avg'] <= 3.8 and p['reviews_count'] >= 2:
            score += 3
            reasons.append('низкий средний рейтинг по синхронизированным отзывам')
        if p['negative_count'] >= 2:
            score += 3
            reasons.append('накопилось несколько негативных отзывов')
        if p['reviews_count'] > 0 and p['negative_count'] / p['reviews_count'] >= 0.3:
            score += 2
            reasons.append('доля негатива ≥30%')
        if any(c['name'] in {'качество', 'качество/брак', 'камень/вставка', 'замок/застежка', 'проба/маркировка'} for c in p['top_categories']):
            score += 2
            reasons.append('есть рискованные категории для производства/качества')
        if p.get('sku'):
            snaps = db.query(RatingSnapshot).filter(RatingSnapshot.platform == p.get('platform'), RatingSnapshot.sku == p['sku']).order_by(desc(RatingSnapshot.created_at)).limit(2).all()
            if len(snaps) == 2:
                try:
                    current = float(snaps[0].rating)
                    previous = float(snaps[1].rating)
                    delta = current - previous
                    p['rating_delta_last_sync'] = round(delta, 2)
                    if abs(delta) >= 0.2:
                        score += 4
                        reasons.append(f'резкое изменение рейтинга за последнюю синхронизацию: {delta:+.2f}')
                except Exception:
                    pass
        if score > 0:
            out.append({**p, 'anomaly_score': score, 'reasons': reasons})
    out.sort(key=lambda x: x['anomaly_score'], reverse=True)
    return out[:50]

@router.get('/cx')
def cx(platform: str | None = Query(None), db: Session = Depends(get_db)):
    return {'products': products_from_reviews(db, platform), 'anomalies': anomalies(platform, None, db)}

@router.get('/sla')
def sla(platform: str | None = Query(None), db: Session = Depends(get_db)):
    """SLA 2.0: only measurable records are used in response-time KPIs.

    Seller-cabinet answers without answer timestamp are counted separately so old
    imported marketplace answers cannot create fake 25k-hour response times.
    """
    p = _norm_platform(platform)
    rq = db.query(Review)
    qq = db.query(Question)
    if p:
        rq = rq.filter(Review.platform == p)
        qq = qq.filter(Question.platform == p)
    reviews = rq.all()
    questions = qq.all()

    def is_unmeasurable_answered(x):
        return bool(x.has_answer or x.final_answer) and x.operational_status != 'needs_response' and x.response_origin == 'seller_cabinet'

    def overdue_needs_response(x, minutes):
        start = x.created_at_marketplace or x.created_at
        if not start or x.operational_status != 'needs_response':
            return False
        return (datetime.utcnow() - start).total_seconds() / 60 > minutes

    overdue_reviews = [r for r in reviews if overdue_needs_response(r, 60)]
    overdue_questions = [q for q in questions if overdue_needs_response(q, 15)]
    unmeasurable_reviews = [r for r in reviews if is_unmeasurable_answered(r)]
    unmeasurable_questions = [q for q in questions if is_unmeasurable_answered(q)]
    return {
        'platform': p or 'ALL',
        'reviews_over_1h': len(overdue_reviews),
        'questions_over_15m': len(overdue_questions),
        'unmeasurable_answered_reviews': len(unmeasurable_reviews),
        'unmeasurable_answered_questions': len(unmeasurable_questions),
        'note': 'Среднее/P90 считаются только по записям с достоверной датой ответа. Ответы из ЛК без даты ответа исключены из SLA скорости.',
        'drilldown': {
            'reviews_over_1h': [_serialize_sla_item(x, 'review') for x in overdue_reviews[:500]],
            'questions_over_15m': [_serialize_sla_item(x, 'question') for x in overdue_questions[:500]],
            'unmeasurable': [_serialize_sla_item(x, 'review') for x in unmeasurable_reviews[:250]] + [_serialize_sla_item(x, 'question') for x in unmeasurable_questions[:250]],
        }
    }


def _serialize_sla_item(x, kind: str):
    start = x.created_at_marketplace or x.created_at
    age_minutes = None
    if start:
        age_minutes = round((datetime.utcnow() - start).total_seconds() / 60, 1)
    return {
        'id': x.id,
        'kind': kind,
        'platform': x.platform,
        'sku': x.sku,
        'product_name': x.product_name,
        'created_at_marketplace': x.created_at_marketplace.isoformat() if x.created_at_marketplace else None,
        'age_minutes': age_minutes,
        'operational_status': x.operational_status,
        'has_answer': x.has_answer,
        'response_origin': x.response_origin,
        'source_status': x.source_status,
        'text': x.text,
    }
