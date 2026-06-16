from __future__ import annotations
import json, random
from openai import OpenAI
from ..config import settings
from .prompts import SYSTEM_PROMPT, RESPONSE_SCHEMA
from .rule_based import classify_review, classify_question, fallback_review_answer, fallback_question_answer
from .quality_gate import strict_gate_result


def _quota_message(exc: Exception) -> bool:
    text = str(exc).lower()
    return 'insufficient_quota' in text or 'exceeded your current quota' in text


def _parse_template_bank(text: str | None) -> dict[str, list[str]]:
    bank: dict[str, list[str]] = {}
    current = None
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.endswith(':') and len(line) <= 80:
            current = line[:-1].strip().lower()
            bank.setdefault(current, [])
        elif current:
            bank[current].append(line)
    return bank



def _fill_placeholders(text: str, payload: dict) -> str:
    product = payload.get('product_name') or payload.get('product') or 'украшение'
    sku = payload.get('sku') or payload.get('vendor_code') or '—'
    client = payload.get('client_name') or ''
    replacements = {
        '{Имя}': client,
        '{Изделие}': product,
        '{Артикул}': str(sku),
        '{Металл/проба}': str(payload.get('metal') or payload.get('material') or ''),
        '{Размер}': str(payload.get('size') or ''),
        '{Тип замка}': str(payload.get('lock_type') or ''),
        '{Вставка}': str(payload.get('insert') or payload.get('stone') or ''),
        '{Ситуация}': str(payload.get('text') or ''),
    }
    for k, v in replacements.items():
        text = text.replace(k, v).replace(k.lower(), v)
    # Убираем обращения вида «, » в начале, если имени нет.
    text = text.replace('{},'.format(client), client + ',' if client else '')
    text = text.replace(' ,', ',').replace('(—)', '').replace('()','')
    return ' '.join(text.split())

def _signature(payload: dict, rules: dict | None) -> str:
    signs = (rules or {}).get('signatures') or ['С уважением, команда KARATOV']
    signs = [str(x).strip() for x in signs if str(x).strip()]
    if not signs:
        signs = ['С уважением, команда KARATOV']
    try:
        idx = int(payload.get('variation_seed') or random.randint(1, 1_000_000)) % len(signs)
    except Exception:
        idx = 0
    return signs[idx]


def _apply_signature(text: str, payload: dict, rules: dict | None) -> str:
    text = (text or '').strip()
    if not text:
        return text
    if 'команда karatov' in text.lower():
        return text
    return text + '\n' + _signature(payload, rules)


def _custom_local_answer(payload: dict, category: str, rules: dict | None, fallback: str) -> str:
    bank = _parse_template_bank((rules or {}).get('local_templates_text'))
    keys = [category.lower(), category.replace('/', ' ').lower(), 'другое']
    for key in keys:
        options = bank.get(key)
        if options:
            try:
                idx = int(payload.get('variation_seed') or 0) % len(options)
            except Exception:
                idx = 0
            return _apply_signature(_fill_placeholders(options[idx], payload), payload, rules)
    return _apply_signature(_fill_placeholders(fallback, payload), payload, rules)


def _has_clear_complaint(payload: dict) -> bool:
    text = ' '.join(str(payload.get(k) or '') for k in ['text', 'pros', 'cons']).lower()
    return any(w in text for w in ['брак', 'дефект', 'слом', 'порвал', 'выпал', 'царап', 'потемн', 'облез', 'отказ', 'возврат', 'не подош', 'не понрав', 'разочар', 'ужас', 'плохо'])


def _effective_template_category(payload: dict, local: dict) -> str:
    rating = payload.get('rating')
    category = (local.get('category') or 'другое')
    sentiment = (local.get('sentiment') or '').lower()
    if rating == 5 and not _has_clear_complaint(payload):
        return 'позитив'
    if rating and rating >= 4 and sentiment in {'positive', 'mixed'} and category in {'тонкое изделие', 'вес изделия', 'упаковка', 'комплектация/бирка', 'позитив с замечанием'}:
        return 'позитив с замечанием'
    return category


class AnswerGenerator:
    def __init__(self, rules: dict | None = None):
        self.client = OpenAI(api_key=settings.openai_api_key) if (settings.openai_api_key and (rules or {}).get('ai_generation_enabled', True)) else None
        self.rules = rules or {}

    def generate_for_review(self, payload: dict) -> dict:
        local = classify_review(payload.get('text'), payload.get('rating'), payload.get('pros'), payload.get('cons'))
        effective_category = _effective_template_category(payload, local)
        if effective_category != local.get('category'):
            local = {**local, 'category': effective_category}
        fallback = _custom_local_answer(payload, effective_category, self.rules, fallback_review_answer(payload, effective_category))
        if not self.client:
            return self._local_review_result(local, fallback, payload, 'OpenAI отключен или API key не задан. Использован локальный шаблон KARATOV из настроек.')
        base_prompt = self.rules.get('review_prompt_template') or 'Сгенерируй ответ на отзыв покупателя. Ответ выдавай только если он уровня 10/10.'
        template_rules = self.rules.get('template_rules_text') or ''
        user_prompt = f"""
{base_prompt}

Правила KARATOV, заданные пользователем в интерфейсе:
{template_rules}

Дополнительная подпись: выбери/используй одну из подписей, но не дублируй, если подпись уже есть: {', '.join(self.rules.get('signatures') or ['С уважением, команда KARATOV'])}

Данные:
Площадка: {payload.get('platform')}
Товар/SKU: {payload.get('sku')}
Название: {payload.get('product_name')}
Оценка: {payload.get('rating')}
Имя клиента: {payload.get('client_name')}
Текст отзыва: {payload.get('text')}
Плюсы: {payload.get('pros')}
Минусы: {payload.get('cons')}

Категория по локальным правилам: {local.get('category')}
Тональность по локальным правилам: {local.get('sentiment')}
Риск по локальным правилам: {local.get('risk_level')}

Верни только JSON по схеме. Не пиши формальный универсальный ответ. Каждый новый запрос должен давать новый естественный вариант, сохраняя смысл и безопасность.
"""
        result = self._call(user_prompt, local, fallback, payload, item_type='review')
        if self.client and not result.get('answer_quality_passed') and result.get('answer_quality_issues') and 'insufficient_quota' not in str(result.get('reason','')):
            repair_prompt = user_prompt + f"""

Предыдущий ответ не прошел проверку 10/10.
Проблемы: {'; '.join(result.get('answer_quality_issues') or [])}
Перепиши ответ так, чтобы он точно был 10/10, но не добавляй обещаний, контактов и спорных формулировок.
"""
            repaired = self._call(repair_prompt, local, fallback, payload, item_type='review')
            if repaired.get('answer_quality_passed'):
                result = repaired
        return result

    def generate_for_question(self, payload: dict) -> dict:
        local = classify_question(payload.get('text'))
        fallback = _custom_local_answer(payload, local['category'], self.rules, fallback_question_answer(payload, local['category']))
        if not self.client:
            return self._local_question_result(local, fallback, payload, 'OpenAI отключен или API key не задан. Использован локальный шаблон KARATOV из настроек.')
        base_prompt = self.rules.get('question_prompt_template') or 'Сгенерируй ответ на вопрос покупателя. Не выдумывай характеристики товара.'
        template_rules = self.rules.get('template_rules_text') or ''
        user_prompt = f"""
{base_prompt}

Правила KARATOV, заданные пользователем в интерфейсе:
{template_rules}

Данные:
Площадка: {payload.get('platform')}
Товар/SKU: {payload.get('sku')}
Название: {payload.get('product_name')}
Имя клиента: {payload.get('client_name')}
Вопрос: {payload.get('text')}

Верни только JSON по схеме.
"""
        return self._call(user_prompt, {**local, 'sentiment': 'neutral'}, fallback, payload, item_type='question')

    def generate_for_review_until_pass(self, payload: dict, max_attempts: int = 10) -> dict:
        """Generate variants until quality gate returns 10/10.

        Used for manual generation and autopublish. If no variant reaches 10/10,
        returns the best/last rejection with a clear reason; it must not be autopublished.
        """
        base_seed = int(payload.get('variation_seed') or random.randint(1, 1_000_000))
        last = None
        for attempt in range(max(1, max_attempts)):
            trial_payload = {**payload, 'variation_seed': base_seed + attempt}
            result = self.generate_for_review(trial_payload)
            result['generation_attempts'] = attempt + 1
            if result.get('answer_quality_passed') and result.get('answer_text'):
                if attempt > 0:
                    result['reason'] = (result.get('reason') or '') + f' Подобран вариант, прошедший 10/10, с попытки {attempt + 1}.'
                return result
            last = result
        if last is None:
            last = {'answer_text': None, 'can_autopublish': False, 'reason': 'Не удалось подобрать шаблон.'}
        last['can_autopublish'] = False
        last['answer_text'] = None
        last['reason'] = (last.get('reason') or '') + f' Не удалось подобрать вариант ответа 10/10 за {max_attempts} попыток; требуется ручная подготовка.'
        last['generation_attempts'] = max_attempts
        return last

    def generate_for_question_until_pass(self, payload: dict, max_attempts: int = 10) -> dict:
        base_seed = int(payload.get('variation_seed') or random.randint(1, 1_000_000))
        last = None
        for attempt in range(max(1, max_attempts)):
            trial_payload = {**payload, 'variation_seed': base_seed + attempt}
            result = self.generate_for_question(trial_payload)
            result['generation_attempts'] = attempt + 1
            if result.get('answer_quality_passed') and result.get('answer_text'):
                if attempt > 0:
                    result['reason'] = (result.get('reason') or '') + f' Подобран вариант, прошедший 10/10, с попытки {attempt + 1}.'
                return result
            last = result
        if last is None:
            last = {'answer_text': None, 'can_autopublish': False, 'reason': 'Не удалось подобрать шаблон.'}
        last['can_autopublish'] = False
        last['answer_text'] = None
        last['reason'] = (last.get('reason') or '') + f' Не удалось подобрать вариант ответа 10/10 за {max_attempts} попыток; требуется ручная подготовка.'
        last['generation_attempts'] = max_attempts
        return last

    def _local_review_result(self, local: dict, fallback: str, payload: dict, note: str) -> dict:
        result = {**local, 'answer_source': 'local_template', 'can_autopublish': bool(local.get('risk_level') == 'low' and (payload.get('rating') or 0) >= 5), 'reason': local.get('reason', '') + ' ' + note, 'answer_text': fallback}
        return strict_gate_result(result, category=local.get('category'), risk_level=local.get('risk_level'), rating=payload.get('rating'), source_text=' '.join(str(payload.get(k) or '') for k in ['text','pros','cons']), min_score=10)

    def _local_question_result(self, local: dict, fallback: str, payload: dict, note: str) -> dict:
        result = {**local, 'sentiment': 'neutral', 'answer_source': 'local_template', 'can_autopublish': bool(local.get('risk_level') == 'low'), 'reason': local.get('reason', '') + ' ' + note, 'answer_text': fallback}
        return strict_gate_result(result, category=local.get('category'), risk_level=local.get('risk_level'), rating=None, source_text=payload.get('text'), min_score=10)

    def _call(self, user_prompt: str, local: dict, fallback_answer: str, payload: dict, item_type: str) -> dict:
        try:
            system = (self.rules.get('custom_system_prompt') or SYSTEM_PROMPT) + '\n' + (self.rules.get('template_rules_text') or '')
            resp = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user_prompt}],
                response_format=RESPONSE_SCHEMA,
                temperature=0.72,
            )
            content = resp.choices[0].message.content or '{}'
            result = json.loads(content)
            risky_categories = {'проба', 'проба/маркировка', 'камень', 'камень/вставка', 'замок/застежка', 'качество', 'качество/брак', 'размер'}
            if result.get('category') in risky_categories or result.get('risk_level') != 'low':
                result['can_autopublish'] = False
            result.setdefault('tags', local.get('tags', []))
            result['answer_text'] = _apply_signature(result.get('answer_text') or '', payload, self.rules)
            result.setdefault('answer_source', 'ai')
            return strict_gate_result(result, category=result.get('category') or local.get('category'), risk_level=result.get('risk_level') or local.get('risk_level'), rating=payload.get('rating') if item_type == 'review' else None, source_text=' '.join(str(payload.get(k) or '') for k in ['text','pros','cons']), min_score=10)
        except Exception as exc:
            reason = f'OpenAI недоступен: {exc}'
            if _quota_message(exc):
                reason = 'OpenAI API вернул insufficient_quota: нужно проверить billing/кредиты в OpenAI Platform. Использован локальный шаблон KARATOV из настроек, затем пропущен через quality gate 10/10.'
            if not self.rules.get('ai_fallback_to_local_templates', True):
                return {**local, 'can_autopublish': False, 'reason': reason + ' Fallback на локальные шаблоны выключен в настройках.', 'answer_text': None, 'answer_source': 'ai_error_no_fallback'}
            local_result = {**local, 'can_autopublish': False, 'reason': reason, 'answer_text': fallback_answer, 'answer_source': 'fallback_local_template'}
            return strict_gate_result(local_result, category=local.get('category'), risk_level=local.get('risk_level'), rating=payload.get('rating') if item_type == 'review' else None, source_text=' '.join(str(payload.get(k) or '') for k in ['text','pros','cons']), min_score=10)
