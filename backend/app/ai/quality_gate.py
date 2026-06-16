from __future__ import annotations
import re
from typing import Any

FORBIDDEN_HARD = [
    'обменяем', 'вернем деньги', 'вернём деньги', 'гарантируем возврат', 'подделка',
    'вы неправы', 'сами виноваты', 'это невозможно', 'такого быть не может',
    'пишите нам в whatsapp', 'пишите нам в вотсап', 'наш телефон', 'email', 'почта',
]

CATEGORY_NEEDS = {
    'позитив': ['спасибо'],
    'размер': ['размер', 'посад'],
    'проба': ['контроль', 'маркиров'],
    'проба/маркировка': ['контроль', 'маркиров'],
    'замок/застежка': ['заст', 'команд'],
    'камень': ['встав', 'команд'],
    'камень/вставка': ['встав', 'команд'],
    'качество': ['качеств', 'команд'],
    'качество/брак': ['качеств', 'команд'],
    'деформация': ['качеств', 'команд'],
    'цепь/плетение': ['качеств', 'команд'],
    'позитив с замечанием': ['спасибо'],
    'ожидание/реальность': ['ожидан', 'карточ'],
    'упаковка': ['упаков'],
    'доставка': ['достав'],
    'другое': ['спасибо'],
}

CHECKS = [
    'безопасность: нет запрещенных обещаний/контактов/спора',
    'тон: теплый, человеческий, не канцелярский',
    'контекст: ответ учитывает смысл отзыва/категорию',
    'бренд: аккуратная позиция KARATOV без признания несуществующих фактов',
    'маршрут: для риска есть проверка/поддержка/инструменты маркетплейса',
    'краткость: 1–4 предложения, без воды',
    'конкретика: нет пустой универсальной фразы без привязки к отзыву',
    'площадка: нет ссылок, телефонов и внешних каналов',
    'категории проба/качество: нет дискредитации изделия',
    'публикационная готовность: текст можно отправлять без стыда и правок',
]

def _sentences(text: str) -> int:
    parts = [p.strip() for p in re.split(r'[.!?]+', text or '') if p.strip()]
    return len(parts)

def evaluate_answer(answer: str | None, *, category: str | None = None, risk_level: str | None = None, rating: int | None = None, source_text: str | None = None) -> dict[str, Any]:
    answer = (answer or '').strip()
    low = answer.lower()
    cat = (category or 'другое').lower()
    score = 10
    issues: list[str] = []

    if not answer:
        score -= 10; issues.append('Нет текста ответа.')
    if len(answer) < 35:
        score -= 2; issues.append('Ответ слишком короткий и выглядит формальным.')
    if len(answer) > 750:
        score -= 2; issues.append('Ответ слишком длинный для маркетплейса.')
    if _sentences(answer) > 4:
        score -= 1; issues.append('Ответ длиннее 4 предложений.')
    if any(x in low for x in FORBIDDEN_HARD):
        score -= 5; issues.append('Есть запрещенная/опасная формулировка.')
    if 'ваше мнение очень важно' in low:
        score -= 1; issues.append('Слишком шаблонная канцелярская фраза.')
    if 'спасибо' not in low and 'благодар' not in low and cat != 'доставка':
        score -= 1; issues.append('Нет благодарности/человеческого начала.')

    needs = CATEGORY_NEEDS.get(cat, [])
    if needs and not any(n in low for n in needs):
        score -= 2; issues.append(f'Ответ не отражает категорию «{cat}».')
    if cat in {'проба', 'проба/маркировка', 'камень', 'камень/вставка', 'замок/застежка', 'качество', 'качество/брак', 'деформация', 'цепь/плетение'}:
        if not any(x in low for x in ['провер', 'разбер', 'команд', 'поддерж', 'маркетплейс']):
            score -= 2; issues.append('Для рискованной категории нет аккуратного маршрута проверки.')
    if cat in {'проба', 'проба/маркировка'}:
        if not any(x in low for x in ['маркиров', 'контроль', 'проб']):
            score -= 2; issues.append('Для темы пробы нет безопасного объяснения про контроль/маркировку.')
    if rating is not None and rating <= 3 and not any(x in low for x in ['жаль', 'сожале', 'нам важно', 'разобраться']):
        score -= 1; issues.append('Для низкой оценки не хватает эмпатии.')
    source_low = (source_text or '').lower()
    clear_complaint = any(x in source_low for x in ['брак', 'дефект', 'слом', 'выпал', 'отказ', 'возврат', 'не подош', 'не понрав'])
    if rating == 5 and cat in {'позитив', 'позитив с замечанием'} and not clear_complaint and any(x in low for x in ['жаль', 'проблем', 'обращение', 'команде качества', 'ситуац']):
        score -= 4; issues.append('Для позитивного отзыва 5★ ответ звучит как реакция на проблему.')

    score = max(0, min(10, score))
    return {'score': score, 'passed': score == 10, 'issues': issues, 'checklist': CHECKS}

def strict_gate_result(result: dict[str, Any], *, category: str | None, risk_level: str | None, rating: int | None, source_text: str | None, min_score: int = 10) -> dict[str, Any]:
    quality = evaluate_answer(result.get('answer_text'), category=category or result.get('category'), risk_level=risk_level or result.get('risk_level'), rating=rating, source_text=source_text)
    result['answer_quality_score'] = quality['score']
    result['answer_quality_passed'] = bool(quality['score'] >= min_score and quality['passed'])
    result['answer_quality_issues'] = quality['issues']
    result['answer_quality_checklist'] = quality['checklist']
    if not result['answer_quality_passed']:
        result['can_autopublish'] = False
        result['answer_text'] = ''
        base = result.get('reason') or ''
        result['reason'] = (base + f' Quality gate: ответ не выдан, оценка {quality["score"]}/10. Нужно 10/10. ' + ' '.join(quality['issues'])).strip()
    else:
        base = result.get('reason') or ''
        result['reason'] = (base + ' Quality gate: ответ прошел проверку 10/10.').strip()
    return result
