from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

import httpx

WB_BASE = 'https://feedbacks-api.wildberries.ru'

# RC1.2: a single in-process WB request gate without global 429 circuit breaker.
# The gate prevents parallel WB requests, while 429 cooldown is handled per scheduler block.
_WB_GATE_LOCK = asyncio.Lock()
_WB_NEXT_ALLOWED_AT = 0.0
_WB_ADAPTIVE_INTERVAL_SECONDS = 0.0

class WildberriesClient:
    def __init__(
        self,
        token: str,
        max_retries: int = 5,
        base_delay_seconds: float = 15,
        request_pause_seconds: float = 1.5,
        request_timeout_seconds: float = 20,
    ):
        if not token:
            raise ValueError('WB_API_TOKEN не заполнен')
        self.token = token
        self.headers = {'Authorization': token, 'Content-Type': 'application/json'}
        self.max_retries = max(0, int(max_retries))
        self.base_delay_seconds = max(1.0, float(base_delay_seconds))
        self.request_pause_seconds = max(0.0, float(request_pause_seconds))
        self.request_timeout_seconds = max(5.0, float(request_timeout_seconds))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """WB API request with automatic retry for rate limits and transient errors."""
        url = f'{WB_BASE}{path}'
        last_error: str | None = None

        timeout = httpx.Timeout(self.request_timeout_seconds, connect=min(10.0, self.request_timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(self.max_retries + 1):
                if attempt > 0:
                    delay = self.base_delay_seconds * attempt
                    await asyncio.sleep(delay)

                try:
                    # v3.8: all WB requests go through one global gate. This is the single
                    # correct fix for 429 storms: no parallel WB requests from sync/backfill/publish.
                    await self._wait_wb_global_gate()
                    response = await client.request(
                        method,
                        url,
                        headers=self.headers if method in {'POST', 'PATCH'} else {'Authorization': self.token},
                        params=params,
                        json=payload,
                    )
                    await self._mark_wb_request_sent()

                    if response.status_code == 429:
                        retry_after = response.headers.get('Retry-After')
                        delay = self._parse_retry_after(retry_after)
                        if delay:
                            global _WB_ADAPTIVE_INTERVAL_SECONDS
                            _WB_ADAPTIVE_INTERVAL_SECONDS = min(max(delay, _WB_ADAPTIVE_INTERVAL_SECONDS, 30.0), 300.0)
                        last_error = 'WB вернул 429 Too Many Requests. Cooldown должен применяться только к текущему WB-блоку.'
                        raise RuntimeError(last_error)

                    if 500 <= response.status_code < 600 and attempt < self.max_retries:
                        last_error = f'WB временно недоступен: {response.status_code}. Повторяем запрос.'
                        continue

                    response.raise_for_status()

                    if not response.content:
                        return None
                    parsed = response.json()
                    if isinstance(parsed, dict) and parsed.get('error') is True:
                        raise RuntimeError(f"WB API вернул error=true: {parsed.get('errorText') or parsed.get('additionalErrors') or parsed}")
                    await self._relax_wb_adaptive_interval()
                    return parsed

                except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                    last_error = f'Сетевая ошибка WB API: {type(exc).__name__}'
                    if attempt < self.max_retries:
                        continue
                    raise RuntimeError(f'{last_error}. Попытки закончились.') from exc
                except httpx.HTTPStatusError as exc:
                    body = exc.response.text[:1000] if exc.response is not None else ''
                    raise RuntimeError(
                        f'WB API вернул ошибку {exc.response.status_code}: {body or last_error or exc}'
                    ) from exc

        raise RuntimeError(last_error or 'Не удалось выполнить запрос WB API')


    def _configured_min_interval_seconds(self) -> float:
        # Import settings lazily to avoid circular imports during app startup.
        try:
            from ..config import settings  # type: ignore
            configured = float(getattr(settings, 'wb_global_min_request_interval_seconds', 12))
        except Exception:
            configured = 12.0
        return max(float(self.request_pause_seconds), configured, 1.0)

    async def _wait_wb_global_gate(self) -> None:
        global _WB_NEXT_ALLOWED_AT, _WB_ADAPTIVE_INTERVAL_SECONDS
        async with _WB_GATE_LOCK:
            now = time.monotonic()
            wait_for = max(0.0, _WB_NEXT_ALLOWED_AT - now)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            interval = max(self._configured_min_interval_seconds(), _WB_ADAPTIVE_INTERVAL_SECONDS)
            _WB_NEXT_ALLOWED_AT = time.monotonic() + interval

    async def _mark_wb_request_sent(self) -> None:
        # Reserved hook: _wait_wb_global_gate already moves next_allowed before sending.
        return None

    async def _relax_wb_adaptive_interval(self) -> None:
        global _WB_ADAPTIVE_INTERVAL_SECONDS
        async with _WB_GATE_LOCK:
            base = self._configured_min_interval_seconds()
            if _WB_ADAPTIVE_INTERVAL_SECONDS > base:
                _WB_ADAPTIVE_INTERVAL_SECONDS = max(base, _WB_ADAPTIVE_INTERVAL_SECONDS * 0.9)

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(1.0, float(value))
        except ValueError:
            return None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        data = await self._request('GET', path, params=params)
        return data or {}

    async def _post(self, path: str, payload: dict[str, Any]) -> None:
        await self._request('POST', path, payload=payload)

    async def _patch(self, path: str, payload: dict[str, Any]) -> None:
        await self._request('PATCH', path, payload=payload)

    def _extract_items(self, data: dict[str, Any], primary_key: str) -> list[dict[str, Any]]:
        """WB may wrap arrays differently. This keeps the connector tolerant to response shape changes."""
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
            # common fallback names
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
        # WB can move answered/old/no-text feedbacks to archive, so we sync it too.
        data = await self._get('/api/v1/feedbacks/archive', {'take': take, 'skip': skip, 'order': 'dateDesc'})
        return self._extract_items(data, 'feedbacks')

    async def get_questions(self, is_answered: bool = False, take: int = 50, skip: int = 0) -> list[dict[str, Any]]:
        data = await self._get('/api/v1/questions', {'isAnswered': str(is_answered).lower(), 'take': take, 'skip': skip, 'order': 'dateDesc'})
        return self._extract_items(data, 'questions')

    async def get_questions_unanswered_count(self) -> dict[str, Any]:
        try:
            return await self._get('/api/v1/questions/count-unanswered')
        except Exception as exc:
            return {'error': str(exc)}

    async def get_questions_count(self) -> dict[str, Any]:
        try:
            return await self._get('/api/v1/questions/count')
        except Exception as exc:
            return {'error': str(exc)}

    async def get_feedbacks_count(self, is_answered: bool | None = None) -> dict[str, Any]:
        try:
            params = {} if is_answered is None else {'isAnswered': str(is_answered).lower()}
            return await self._get('/api/v1/feedbacks/count', params)
        except Exception as exc:
            return {'error': str(exc)}

    async def get_feedbacks_unanswered_count(self) -> dict[str, Any]:
        try:
            return await self._get('/api/v1/feedbacks/count-unanswered')
        except Exception as exc:
            return {'error': str(exc)}

    async def answer_feedback(self, feedback_id: str, text: str) -> None:
        await self._post('/api/v1/feedbacks/answer', {'id': feedback_id, 'text': text})

    async def edit_feedback_answer(self, feedback_id: str, text: str) -> None:
        # WB: редактирование уже опубликованного ответа на отзыв.
        # Если WB ограничит редактирование по сроку/количеству, ошибка вернется в интерфейс.
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
        # If answer object exists but text is hidden/empty, still treat as answered.
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
    if state in {'answered', 'answer', 'has_answer'}:
        return True
    return False


def _product_details(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get('productDetails') or item.get('product') or item.get('nomenclature') or item.get('nm')
    return details if isinstance(details, dict) else {}


def _walk_values(obj: Any, keys: set[str]) -> Any:
    """Recursively search WB payload for product identifiers/names.
    WB returns product data in slightly different shapes for feedbacks, archive and questions.
    """
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
        'final_answer': _answer_text(item.get('answer')) or _answer_text(item.get('answerText')) or _answer_text(item.get('supplierAnswer')),
        'raw': {**item, '_sync_source': source},
    }


def normalize_question(item: dict[str, Any]) -> dict[str, Any]:
    return {
        'platform': 'WB',
        'external_id': str(item.get('id')),
        'sku': _sku(item),
        'product_name': _product_name(item),
        'text': item.get('text') or item.get('question') or item.get('questionText'),
        'client_name': item.get('userName') or item.get('clientName'),
        'created_at_marketplace': parse_wb_dt(item.get('createdDate') or item.get('createdDateTime') or item.get('createdAt')),
        'has_answer': _has_answer(item),
        'final_answer': _answer_text(item.get('answer')) or _answer_text(item.get('answerText')) or _answer_text(item.get('supplierAnswer')),
        'raw': item,
    }
