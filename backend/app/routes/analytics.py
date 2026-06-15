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
