from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from typing import Any

import httpx

WB_BASE = 'https://feedbacks-api.wildberries.ru'
_WB_GATE_LOCK = asyncio.Lock()
_WB_NEXT_ALLOWED_AT = 0.0


class WbRateLimitError(RuntimeError):
    def __init__(self, message: str, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class WildberriesClient:
    def __init__(
        self,
        token: str,
        max_retries: int = 2,
        base_delay_seconds: float = 5,
        request_pause_seconds: float = 2,
        request_timeout_seconds: float = 20,
    ):
        if not token:
            raise ValueError('WB_API_TOKEN не заполнен')
        self.token = token
        self.max_retries = max(0, int(max_retries))
        self.base_delay_seconds = max(1.0, float(base_delay_seconds))
        self.request_pause_seconds = max(0.0, float(request_pause_seconds))
        self.request_timeout_seconds = max(5.0, float(request_timeout_seconds))

    def _configured_min_interval_seconds(self) -> float:
        try:
            from ..config import settings  # type: ignore
            configured = float(getattr(settings, 'wb_global_min_request_interval_seconds', 2))
        except Exception:
            configured = 2.0
        return max(float(self.request_pause_seconds), configured, 1.0)

    async def _wait_gate(self) -> None:
        global _WB_NEXT_ALLOWED_AT
        async with _WB_GATE_LOCK:
            now = time.monotonic()
            wait_for = max(0.0, _WB_NEXT_ALLOWED_AT - now)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            jitter = random.uniform(0.1, 0.7)
            _WB_NEXT_ALLOWED_AT = time.monotonic() + self._configured_min_interval_seconds() + jitter

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(1.0, float(value))
        except ValueError:
            return None

    def _headers_for_status(self, response: httpx.Response) -> dict[str, Any]:
        return {
            'retry_after': response.headers.get('Retry-After'),
            'x_ratelimit_remaining': response.headers.get('X-Ratelimit-Remaining'),
            'x_ratelimit_retry': response.headers.get('X-Ratelimit-Retry'),
            'x_ratelimit_reset': response.headers.get('X-Ratelimit-Reset'),
        }

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        url = f'{WB_BASE}{path}'
        timeout = httpx.Timeout(self.request_timeout_seconds, connect=min(10.0, self.request_timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(self.max_retries + 1):
                if attempt:
                    await asyncio.sleep(self.base_delay_seconds * attempt + random.uniform(0.2, 1.5))
                await self._wait_gate()
                try:
                    response = await client.request(
                        method,
                        url,
                        headers={'Authorization': self.token, 'Content-Type': 'application/json'} if method in {'POST', 'PATCH'} else {'Authorization': self.token},
                        params=params,
                        json=payload,
                    )
                except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                    if attempt < self.max_retries:
                        continue
                    raise RuntimeError(f'Сетевая ошибка WB API: {type(exc).__name__}') from exc

                rate_headers = self._headers_for_status(response)
                if response.status_code == 429:
                    retry_after = self._parse_retry_after(rate_headers.get('retry_after')) or self._parse_retry_after(rate_headers.get('x_ratelimit_retry'))
                    # Important: no global circuit breaker here. The scheduler will pause only the failed block.
                    raise WbRateLimitError(
                        f'WB вернул 429 Too Many Requests. Блок поставлен на индивидуальную паузу на {retry_after or 120:.0f} сек.',
                        retry_after_seconds=retry_after,
                    )

                if 500 <= response.status_code < 600 and attempt < self.max_retries:
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    body = exc.response.text[:1000] if exc.response is not None else ''
                    raise RuntimeError(f'WB API вернул ошибку {exc.response.status_code}: {body}') from exc
                if not response.content:
                    return None
                parsed = response.json()
                if isinstance(parsed, dict) and parsed.get('error') is True:
                    raise RuntimeError(f"WB API вернул error=true: {parsed.get('errorText') or parsed.get('additionalErrors') or parsed}")
                if isinstance(parsed, dict):
                    parsed.setdefault('_rate_limit_headers', rate_headers)
                return parsed
        raise RuntimeError('Не удалось выполнить запрос WB API')

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request('GET', path, params=params) or {}

    async def _post(self, path: str, payload: dict[str, Any]) -> None:
        await self._request('POST', path, payload=payload)

    async def _patch(self, path: str, payload: dict[str, Any]) -> None:
        await self._request('PATCH', path, payload=payload)

    def _extract_items(self, data: dict[str, Any], primary_key: str) -> list[dict[str, Any]]:
        if not data:
            return []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data.get(primary_key), list):
            return data.get(primary_key) or []
        inner = data.get('data')
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
        if isinstance(inner, dict):
            if isinstance(inner.get(primary_key), list):
                return inner.get(primary_key) or []
            for key in ['feedbacks', 'questions', 'items', 'list', 'result']:
                if isinstance(inner.get(key), list):
                    return inner.get(key) or []
        for key in ['feedbacks', 'questions', 'items', 'list', 'result']:
            if isinstance(data.get(key), list):
                return data.get(key) or []
        return []

    async def get_feedbacks(self, is_answered: bool = False, take: int = 50, skip: int = 0) -> list[dict[str, Any]]:
        data = await self._get('/api/v1/feedbacks', {'isAnswered': str(is_answered).lower(), 'take': take, 'skip': skip, 'order': 'dateDesc'})
        return self._extract_items(data, 'feedbacks')

    async def get_feedbacks_archive(self, take: int = 50, skip: int = 0) -> list[dict[str, Any]]:
        data = await self._get('/api/v1/feedbacks/archive', {'take': take, 'skip': skip, 'order': 'dateDesc'})
        return self._extract_items(data, 'feedbacks')

    async def get_questions(self, is_answered: bool = False, take: int = 50, skip: int = 0) -> list[dict[str, Any]]:
        data = await self._get('/api/v1/questions', {'isAnswered': str(is_answered).lower(), 'take': take, 'skip': skip, 'order': 'dateDesc'})
        return self._extract_items(data, 'questions')

    async def answer_feedback(self, feedback_id: str, text: str) -> None:
        await self._post('/api/v1/feedbacks/answer', {'id': feedback_id, 'text': text})

    async def edit_feedback_answer(self, feedback_id: str, text: str) -> None:
        await self._patch('/api/v1/feedbacks/answer', {'id': feedback_id, 'text': text})

    async def answer_question(self, question_id: str, text: str) -> None:
        await self._patch('/api/v1/questions', {'id': question_id, 'answer': {'text': text}, 'action': 'answer'})


def parse_wb_dt(value: Any):
    if not value:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _answer_text(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ['text', 'answerText', 'content', 'comment']:
            if value.get(key):
                return str(value.get(key)).strip() or None
        if value.get('state') or value.get('id') or value.get('createdDate'):
            return '[answer object exists]'
    if isinstance(value, list) and value:
        return '[answer list exists]'
    return None


def _has_answer(item: dict[str, Any]) -> bool:
    if item.get('isAnswered') is True or item.get('answered') is True or item.get('hasAnswer') is True:
        return True
    if _answer_text(item.get('answer')) or _answer_text(item.get('answerText')) or _answer_text(item.get('supplierAnswer')):
        return True
    state = str(item.get('state') or item.get('answerState') or '').lower()
    return state in {'answered', 'answer', 'has_answer'}


def _product_details(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get('productDetails') or item.get('product') or item.get('nomenclature') or item.get('nm')
    return details if isinstance(details, dict) else {}


def _walk_values(obj: Any, keys: set[str]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj.get(key) not in (None, ''):
                return obj.get(key)
        for value in obj.values():
            found = _walk_values(value, keys)
            if found not in (None, ''):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _walk_values(value, keys)
            if found not in (None, ''):
                return found
    return None


def _sku(item: dict[str, Any]) -> str | None:
    details = _product_details(item)
    value = (
        item.get('nmId') or item.get('nmID') or item.get('nm_id') or item.get('nmid')
        or item.get('imtId') or item.get('imtID')
        or details.get('nmId') or details.get('nmID') or details.get('nm_id') or details.get('nmid')
        or details.get('id') or details.get('imtId')
        or _walk_values(item, {'nmId', 'nmID', 'nm_id', 'nmid', 'imtId', 'imtID', 'subjectId'})
    )
    return str(value) if value not in (None, '') else None


def _product_name(item: dict[str, Any]) -> str | None:
    details = _product_details(item)
    return (
        item.get('productName') or item.get('imtName') or item.get('subjectName') or item.get('name')
        or details.get('productName') or details.get('imtName') or details.get('subjectName')
        or details.get('name') or details.get('goodsName')
        or _walk_values(item, {'productName', 'imtName', 'subjectName', 'name', 'goodsName'})
    )


def normalize_feedback(item: dict[str, Any], source: str = 'feedbacks') -> dict[str, Any]:
    text_parts = [item.get('text') or '', item.get('pros') or '', item.get('cons') or '']
    has_answer = _has_answer(item) or source == 'archive'
    final_answer = _answer_text(item.get('answer')) or _answer_text(item.get('answerText')) or _answer_text(item.get('supplierAnswer'))
    return {
        'platform': 'WB',
        'external_id': str(item.get('id')),
        'sku': _sku(item),
        'product_name': _product_name(item),
        'rating': item.get('productValuation') or item.get('rating') or item.get('valuation'),
        'text': '\n'.join([p for p in text_parts if p]).strip() or None,
        'pros': item.get('pros'),
        'cons': item.get('cons'),
        'client_name': item.get('userName') or item.get('clientName'),
        'created_at_marketplace': parse_wb_dt(item.get('createdDate') or item.get('createdDateTime') or item.get('createdAt')),
        'has_answer': has_answer,
        'final_answer': final_answer,
        'raw': {**item, '_sync_source': source},
    }


def normalize_question(item: dict[str, Any]) -> dict[str, Any]:
    final_answer = _answer_text(item.get('answer')) or _answer_text(item.get('answerText')) or _answer_text(item.get('supplierAnswer'))
    return {
        'platform': 'WB',
        'external_id': str(item.get('id')),
        'sku': _sku(item),
        'product_name': _product_name(item),
        'text': item.get('text') or item.get('question') or item.get('questionText'),
        'client_name': item.get('userName') or item.get('clientName'),
        'created_at_marketplace': parse_wb_dt(item.get('createdDate') or item.get('createdDateTime') or item.get('createdAt')),
        'has_answer': _has_answer(item),
        'final_answer': final_answer,
        'raw': item,
    }
