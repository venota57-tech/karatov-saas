from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Any

import httpx

OZON_API_BASE = 'https://api-seller.ozon.ru'


def _parse_dt(value: Any):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except Exception:
        return None


def _first_present(obj: dict, keys: list[str], default=None):
    for key in keys:
        if key in obj and obj.get(key) not in (None, ''):
            return obj.get(key)
    return default


def _walk(obj: Any, keys: set[str]):
    if isinstance(obj, dict):
        for key in keys:
            if obj.get(key) not in (None, ''):
                return obj.get(key)
        for value in obj.values():
            found = _walk(value, keys)
            if found not in (None, ''):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _walk(value, keys)
            if found not in (None, ''):
                return found
    return None


def normalize_ozon_review(item: dict[str, Any], *, source_status: str, operational_status: str, has_answer: bool) -> dict[str, Any]:
    review_id = _first_present(item, ['id', 'review_id', 'reviewId', 'uuid']) or _walk(item, {'id','review_id','reviewId','uuid'})
    product_id = _first_present(item, ['product_id', 'productId', 'sku', 'offer_id', 'offerId']) or _walk(item, {'product_id','productId','sku','offer_id','offerId'})
    product_name = _first_present(item, ['product_name','productName','product_title','name']) or _walk(item, {'product_name','productName','product_title','name'})
    rating = _first_present(item, ['rating','score','stars'])
    try:
        rating = int(rating) if rating is not None else None
    except Exception:
        rating = None
    text = _first_present(item, ['text','comment','content','message','review_text','reviewText'])
    pros = _first_present(item, ['pros','advantages','positive'])
    cons = _first_present(item, ['cons','disadvantages','negative'])
    answer_text = _first_present(item, ['answer','answer_text','answerText','comment_text','commentText','seller_comment']) or _walk(item, {'answer_text','answerText','comment_text','commentText','seller_comment'})
    created = _first_present(item, ['published_at','created_at','date','createdAt','publishedAt'])
    return {
        'platform': 'OZON',
        'external_id': str(review_id),
        'sku': str(product_id) if product_id is not None else None,
        'product_name': str(product_name) if product_name is not None else None,
        'rating': rating,
        'text': str(text) if text is not None else None,
        'pros': str(pros) if pros is not None else None,
        'cons': str(cons) if cons is not None else None,
        'client_name': _first_present(item, ['author_name','authorName','customer_name','user_name','userName']),
        'created_at_marketplace': _parse_dt(created),
        'has_answer': bool(has_answer or answer_text),
        'final_answer': str(answer_text) if answer_text else None,
        'response_origin': 'seller_cabinet' if bool(has_answer or answer_text) and answer_text else None,
        'raw': item,
        'source_status': source_status,
        'operational_status': operational_status,
        'last_seen_source': source_status,
        'last_seen_at': datetime.utcnow(),
        'publish_blocked_reason': None if operational_status == 'needs_response' else 'Не находится в актуальной очереди Ozon “без ответа”; публикация из этого раздела заблокирована.',
    }


def normalize_ozon_question(item: dict[str, Any], *, source_status: str, operational_status: str, has_answer: bool) -> dict[str, Any]:
    qid = _first_present(item, ['id','question_id','questionId','uuid']) or _walk(item, {'id','question_id','questionId','uuid'})
    product_id = _first_present(item, ['product_id', 'productId', 'sku', 'offer_id', 'offerId']) or _walk(item, {'product_id','productId','sku','offer_id','offerId'})
    product_name = _first_present(item, ['product_name','productName','product_title','name']) or _walk(item, {'product_name','productName','product_title','name'})
    text = _first_present(item, ['text','question','question_text','questionText','message'])
    answer_text = _first_present(item, ['answer','answer_text','answerText']) or _walk(item, {'answer_text','answerText','seller_answer'})
    created = _first_present(item, ['published_at','created_at','date','createdAt','publishedAt'])
    return {
        'platform': 'OZON',
        'external_id': str(qid),
        'sku': str(product_id) if product_id is not None else None,
        'product_name': str(product_name) if product_name is not None else None,
        'text': str(text) if text is not None else None,
        'client_name': _first_present(item, ['author_name','authorName','customer_name','user_name','userName']),
        'created_at_marketplace': _parse_dt(created),
        'has_answer': bool(has_answer or answer_text),
        'final_answer': str(answer_text) if answer_text else None,
        'response_origin': 'seller_cabinet' if bool(has_answer or answer_text) and answer_text else None,
        'raw': item,
        'source_status': source_status,
        'operational_status': operational_status,
        'last_seen_source': source_status,
        'last_seen_at': datetime.utcnow(),
        'publish_blocked_reason': None if operational_status == 'needs_response' else 'Не находится в актуальной очереди Ozon “без ответа”; публикация из этого раздела заблокирована.',
    }


class OzonClient:
    def __init__(self, client_id: str, api_key: str, *, request_timeout_seconds: float = 30, request_pause_seconds: float = 1.0):
        self.client_id = client_id
        self.api_key = api_key
        self.request_timeout_seconds = request_timeout_seconds
        self.request_pause_seconds = request_pause_seconds

    def _headers(self):
        return {'Client-Id': self.client_id, 'Api-Key': self.api_key, 'Content-Type': 'application/json'}

    async def _post(self, path: str, payload: dict[str, Any]):
        if not self.client_id or not self.api_key:
            raise RuntimeError('OZON_CLIENT_ID/OZON_API_KEY не заполнены в .env')
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.post(f'{OZON_API_BASE}{path}', headers=self._headers(), json=payload)
        await asyncio.sleep(self.request_pause_seconds)
        if response.status_code >= 400:
            raise RuntimeError(f'Ozon API {path} HTTP {response.status_code}: {response.text[:800]}')
        return response.json()

    @staticmethod
    def _extract_items(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if not isinstance(data, dict):
            return []
        result = data.get('result', data)
        if isinstance(result, list):
            return [x for x in result if isinstance(x, dict)]
        if isinstance(result, dict):
            for key in ['reviews','items','questions','list','data']:
                value = result.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
        for key in ['reviews','items','questions','list','data']:
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return []

    def _item_id(self, item: dict[str, Any], keys: set[str]) -> str | None:
        value = _walk(item, keys)
        return str(value) if value not in (None, '') else None

    async def _enrich_items(self, items: list[dict[str, Any]], *, info_path: str | None, id_keys: set[str], id_field: str, max_items: int = 100) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not info_path:
            return items, {'enriched': 0, 'info_errors': 0}
        enriched: list[dict[str, Any]] = []
        info_errors = 0
        for item in items[:max_items]:
            ext_id = self._item_id(item, id_keys)
            if not ext_id:
                enriched.append(item)
                continue
            try:
                info = await self._post(info_path, {id_field: ext_id})
                result = info.get('result', info) if isinstance(info, dict) else info
                if isinstance(result, dict):
                    merged = dict(item)
                    merged.update(result)
                    # Keep the list payload too, because sometimes info has fewer product fields.
                    merged['_list_payload'] = item
                    enriched.append(merged)
                else:
                    enriched.append(item)
            except Exception:
                info_errors += 1
                enriched.append(item)
        if len(items) > max_items:
            enriched.extend(items[max_items:])
        return enriched, {'enriched': len(enriched) - info_errors, 'info_errors': info_errors, 'info_path': info_path}

    async def _try_payloads(self, path: str, payloads: list[dict[str, Any]], *, info_path: str | None = None, id_keys: set[str] | None = None, id_field: str = 'id') -> tuple[list[dict[str, Any]], dict[str, Any]]:
        attempts = []
        for payload in payloads:
            try:
                data = await self._post(path, payload)
                items = self._extract_items(data)
                enrich_diag = {'enriched': 0, 'info_errors': 0}
                if items and info_path and id_keys:
                    items, enrich_diag = await self._enrich_items(items, info_path=info_path, id_keys=id_keys, id_field=id_field, max_items=int(payload.get('limit') or len(items)))
                return items, {'endpoint': path, 'payload': payload, 'raw_keys': list(data.keys()) if isinstance(data, dict) else type(data).__name__, 'received': len(items), **enrich_diag}
            except Exception as exc:
                attempts.append({'endpoint': path, 'payload': payload, 'error': str(exc)[:1000]})
        raise RuntimeError({'attempts': attempts})

    async def get_reviews_unanswered(self, limit: int = 100):
        payloads = [
            {'limit': limit, 'status': 'UNPROCESSED'},
            {'filter': {'status': 'UNPROCESSED'}, 'limit': limit},
            {'filter': {'statuses': ['UNPROCESSED']}, 'limit': limit},
        ]
        return await self._try_payloads('/v1/review/list', payloads, info_path='/v1/review/info', id_keys={'id','review_id','reviewId','uuid'}, id_field='review_id')

    async def get_reviews_answered(self, limit: int = 100):
        payloads = [
            {'limit': limit, 'status': 'PROCESSED'},
            {'filter': {'status': 'PROCESSED'}, 'limit': limit},
            {'filter': {'statuses': ['PROCESSED']}, 'limit': limit},
            {'limit': limit},
        ]
        return await self._try_payloads('/v1/review/list', payloads, info_path='/v1/review/info', id_keys={'id','review_id','reviewId','uuid'}, id_field='review_id')

    async def get_questions_unanswered(self, limit: int = 100):
        payloads = [
            {'limit': limit, 'status': 'UNPROCESSED'},
            {'limit': limit, 'status': 'NEW'},
            {'filter': {'status': 'UNPROCESSED'}, 'limit': limit},
            {'filter': {'statuses': ['UNPROCESSED']}, 'limit': limit},
        ]
        return await self._try_payloads('/v1/question/list', payloads, info_path='/v1/question/info', id_keys={'id','question_id','questionId','uuid'}, id_field='question_id')

    async def get_questions_answered(self, limit: int = 100):
        payloads = [
            {'limit': limit, 'status': 'PROCESSED'},
            {'filter': {'status': 'PROCESSED'}, 'limit': limit},
            {'filter': {'statuses': ['PROCESSED']}, 'limit': limit},
            {'limit': limit},
        ]
        return await self._try_payloads('/v1/question/list', payloads, info_path='/v1/question/info', id_keys={'id','question_id','questionId','uuid'}, id_field='question_id')

    async def publish_review_answer(self, review_id: str, text: str):
        payloads = [
            {'review_id': review_id, 'text': text},
            {'review_id': review_id, 'comment': text},
        ]
        return await self._try_payloads('/v1/review/comment/create', payloads)

    async def publish_question_answer(self, question_id: str, text: str):
        payloads = [
            {'question_id': question_id, 'text': text},
            {'question_id': question_id, 'answer': text},
        ]
        return await self._try_payloads('/v1/question/answer/create', payloads)
