from __future__ import annotations
from datetime import datetime, date, timedelta
import csv, io
from collections import defaultdict
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from ..database import get_db
from ..models import Review, Question, wb_product_url_from_raw

router = APIRouter(prefix='/reports', tags=['reports'])

def _date(dt):
    if not dt: return None
    return dt.date() if hasattr(dt, 'date') else None

def _is_answered_status(status: str | None, source_status: str | None):
    st = (status or '').lower(); ss = (source_status or '').lower()
    return st in {'published','auto_published','answered','local_edited'} or ss in {'wb_answered','wb_archive'}

def _answer_dt(row):
    # Для опубликованных из нашего интерфейса updated_at — ближайшее доступное время ответа.
    # Для уже отвеченных из WB точного времени ответа API может не отдать, тогда считаем по updated_at локальной синхронизации.
    return getattr(row, 'updated_at', None)

def _sla_bucket(row, minutes: int):
    start = getattr(row, 'created_at_marketplace', None)
    finish = _answer_dt(row)
    if not start or not finish:
        return 'unknown'
    delta = finish - start
    return 'within' if delta <= timedelta(minutes=minutes) else 'over'

def _product_url(row):
    return wb_product_url_from_raw(row.raw, row.sku)

@router.get('/daily')
def daily_report(db: Session = Depends(get_db)):
    reviews = db.query(Review).all()
    questions = db.query(Question).all()
    days = defaultdict(lambda: {
        'date': None,
        'reviews_received': 0,
        'questions_received': 0,
        'reviews_answered': 0,
        'questions_answered': 0,
        'reviews_answered_within_1h': 0,
        'reviews_answered_over_1h': 0,
        'reviews_answered_unknown_time': 0,
        'questions_answered_within_15m': 0,
        'questions_answered_over_15m': 0,
        'questions_answered_unknown_time': 0,
    })
    for r in reviews:
        d = _date(r.created_at_marketplace) or _date(r.created_at)
        if d:
            key = d.isoformat(); days[key]['date'] = key; days[key]['reviews_received'] += 1
        if _is_answered_status(r.status, r.source_status):
            ad = _date(_answer_dt(r)) or d
            if ad:
                key = ad.isoformat(); days[key]['date'] = key; days[key]['reviews_answered'] += 1
                b = _sla_bucket(r, 60)
                days[key]['reviews_answered_within_1h' if b == 'within' else 'reviews_answered_over_1h' if b == 'over' else 'reviews_answered_unknown_time'] += 1
    for q in questions:
        d = _date(q.created_at_marketplace) or _date(q.created_at)
        if d:
            key = d.isoformat(); days[key]['date'] = key; days[key]['questions_received'] += 1
        if _is_answered_status(q.status, q.source_status):
            ad = _date(_answer_dt(q)) or d
            if ad:
                key = ad.isoformat(); days[key]['date'] = key; days[key]['questions_answered'] += 1
                b = _sla_bucket(q, 15)
                days[key]['questions_answered_within_15m' if b == 'within' else 'questions_answered_over_15m' if b == 'over' else 'questions_answered_unknown_time'] += 1
    return sorted(days.values(), key=lambda x: x['date'] or '', reverse=True)

@router.get('/pivot')
def pivot_report(db: Session = Depends(get_db)):
    reviews = db.query(Review).all()
    by_product = defaultdict(lambda: defaultdict(lambda: {'total':0, 'negative':0, 'positive':0, 'rating_sum':0, 'rating_count':0, 'product_name':None, 'sku':None, 'product_url':None}))
    by_category = defaultdict(lambda: defaultdict(int))
    for r in reviews:
        day = (_date(r.created_at_marketplace) or _date(r.created_at) or date.today()).isoformat()
        key = r.sku or r.product_name or f'unknown:{r.external_id}'
        row = by_product[key][day]
        row['sku'] = r.sku; row['product_name'] = r.product_name; row['product_url'] = _product_url(r)
        row['total'] += 1
        if r.rating is not None:
            row['rating_sum'] += r.rating; row['rating_count'] += 1
            if r.rating <= 3: row['negative'] += 1
            if r.rating >= 4: row['positive'] += 1
        by_category[r.ai_category or 'не классифицировано'][day] += 1
    products = []
    for key, days in by_product.items():
        for day, row in days.items():
            products.append({
                'date': day, 'product_key': key, 'sku': row['sku'], 'product_name': row['product_name'], 'product_url': row['product_url'],
                'reviews': row['total'], 'negative': row['negative'], 'positive': row['positive'],
                'rating_avg': round(row['rating_sum']/row['rating_count'], 2) if row['rating_count'] else None,
            })
    categories = [{'category': cat, 'date': day, 'count': count} for cat, days in by_category.items() for day, count in days.items()]
    products.sort(key=lambda x: (x['date'], x['negative'], x['reviews']), reverse=True)
    categories.sort(key=lambda x: (x['date'], x['count']), reverse=True)
    return {'products_dynamic': products, 'categories_dynamic': categories}

@router.get('/text')
def text_report(db: Session = Depends(get_db)):
    total_reviews = db.query(func.count(Review.id)).scalar() or 0
    total_questions = db.query(func.count(Question.id)).scalar() or 0
    negative = db.query(func.count(Review.id)).filter(Review.rating <= 3).scalar() or 0
    cats = db.query(Review.ai_category, func.count(Review.id)).group_by(Review.ai_category).order_by(func.count(Review.id).desc()).limit(8).all()
    top = ', '.join([f'{c or "не классифицировано"}: {n}' for c,n in cats]) or 'нет данных'
    daily = daily_report(db)[:1]
    today = daily[0] if daily else {}
    report = f"""CX-сводка KARATOV по WB\n\nВсего отзывов в базе: {total_reviews}\nВсего вопросов в базе: {total_questions}\nНегативных отзывов 1–3★: {negative}\nОсновные категории отзывов: {top}\n\nПоследняя дата в отчете: {today.get('date','—')}\nПоступило отзывов: {today.get('reviews_received',0)}\nПоступило вопросов: {today.get('questions_received',0)}\nОтвечено отзывов: {today.get('reviews_answered',0)}\nОтвечено вопросов: {today.get('questions_answered',0)}\nОтзывы ≤1 часа: {today.get('reviews_answered_within_1h',0)}, >1 часа: {today.get('reviews_answered_over_1h',0)}\nВопросы ≤15 минут: {today.get('questions_answered_within_15m',0)}, >15 минут: {today.get('questions_answered_over_15m',0)}\n"""
    return {'report': report}

@router.get('/export/daily.csv')
def export_daily_csv(db: Session = Depends(get_db)):
    rows = daily_report(db)
    out = io.StringIO()
    fieldnames = ['date','reviews_received','questions_received','reviews_answered','questions_answered','reviews_answered_within_1h','reviews_answered_over_1h','reviews_answered_unknown_time','questions_answered_within_15m','questions_answered_over_15m','questions_answered_unknown_time']
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader(); writer.writerows(rows)
    return Response(out.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename="karatov_daily_report.csv"'})

@router.get('/export/pivot.csv')
def export_pivot_csv(db: Session = Depends(get_db)):
    data = pivot_report(db)['products_dynamic']
    out = io.StringIO()
    fieldnames = ['date','product_key','sku','product_name','product_url','reviews','negative','positive','rating_avg']
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader(); writer.writerows(data)
    return Response(out.getvalue(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':'attachment; filename="karatov_product_dynamic_report.csv"'})
