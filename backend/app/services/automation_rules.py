from __future__ import annotations

from sqlalchemy.orm import Session
from ..models import AutomationRules
from ..ai.template_library import KARATOV_LOCAL_TEMPLATES, KARATOV_TEMPLATE_RULES, KARATOV_SYSTEM_PROMPT, TEMPLATE_LIBRARY_VERSION

DEFAULT_CATEGORIES = [
    'позитив', 'размер', 'посадка', 'тонкое изделие', 'вес изделия', 'замок/застежка',
    'камень/вставка', 'качество/брак', 'деформация', 'цепь/плетение', 'проба/маркировка',
    'цвет/покрытие', 'ожидание/реальность', 'упаковка', 'комплектация/бирка', 'доставка',
    'цена/ценность', 'отказ/возврат', 'вопрос по характеристикам', 'другое'
]

DEFAULT_LOCAL_TEMPLATES = KARATOV_LOCAL_TEMPLATES

DEFAULT_RULES = {
    'autopublish_positive_reviews': False,
    'positive_review_min_rating': 5,
    'autopublish_questions': False,
    'require_review_categories': ['проба/маркировка', 'камень/вставка', 'замок/застежка', 'качество/брак', 'размер'],
    'require_review_risk_levels': ['medium', 'high'],
    'forbidden_phrases': ['обменяем', 'гарантируем возврат', 'подделка', 'вы неправы', 'сами виноваты'],
    'max_auto_answer_chars': 900,
    'real_autopublish_enabled': False,
    'auto_generate_on_sync': True,
    'autopublish_local_templates': True,
    'autopublish_max_per_run': 10,
    'autopublish_interval_seconds': 900,
    'autopublish_pause_between_items_seconds': 8,
    'ai_generation_enabled': True,
    'ai_fallback_to_local_templates': True,
    'autopublish_matrix': {
        'WB': {'reviews': False, 'questions': False},
        'OZON': {'reviews': False, 'questions': False},
        'YM': {'reviews': False, 'questions': False},
    },

    # v2.5: editable AI training/settings layer
    'custom_system_prompt': KARATOV_SYSTEM_PROMPT,
    'review_prompt_template': 'Сгенерируй ответ на отзыв покупателя. Учитывай оценку, текст, плюсы/минусы, категорию, риск и правила KARATOV. Ответ должен быть готов к публикации только если качество 10/10.',
    'question_prompt_template': 'Сгенерируй ответ на вопрос покупателя. Не выдумывай характеристики товара. Если данных не хватает — мягко укажи, что информация требует проверки или нужно ориентироваться на карточку товара.',
    'template_rules_text': KARATOV_TEMPLATE_RULES,
    'local_templates_text': DEFAULT_LOCAL_TEMPLATES,
    'template_library_version': TEMPLATE_LIBRARY_VERSION,
    'signatures': ['С уважением, команда KARATOV', 'С заботой, команда KARATOV', 'Команда KARATOV'],
    'expanded_review_categories': DEFAULT_CATEGORIES,
    'category_keywords_text': 'размер: размер, маломерит, большемерит, тесно, большой, маленький, не подошел\nзамок/застежка: замок, застежка, карабин, не застегивается, сломался\nкамень/вставка: камень, вставка, фианит, выпал, шатается\nкачество/брак: брак, дефект, качество, сломалось, порвалось, потемнело, погнулось\nпроба/маркировка: проба, клеймо, маркировка, 585, 925\nупаковка: упаковка, коробка, пакет, бирка\nдоставка: доставка, курьер, пвз, задержка\nожидание/реальность: фото, не как на фото, цвет, отличается, ожидала',
}


def get_rules(db: Session) -> AutomationRules:
    row = db.query(AutomationRules).filter(AutomationRules.name == 'default').first()
    if row:
        changed = False
        existing = row.rules or {}
        rules = dict(DEFAULT_RULES)
        rules.update(existing)
        # v2.7: обновляем системную библиотеку шаблонов, если пользователь еще не зафиксировал свою вручную.
        # Так новые правила KARATOV попадут в уже созданную локальную базу после обновления версии.
        if existing.get('template_library_version') != TEMPLATE_LIBRARY_VERSION and not existing.get('custom_templates_locked'):
            rules['local_templates_text'] = DEFAULT_LOCAL_TEMPLATES
            rules['template_rules_text'] = KARATOV_TEMPLATE_RULES
            rules['custom_system_prompt'] = KARATOV_SYSTEM_PROMPT
            rules['template_library_version'] = TEMPLATE_LIBRARY_VERSION
            changed = True
        if rules != existing:
            row.rules = rules
            changed = True
        if changed:
            db.commit(); db.refresh(row)
        return row
    row = AutomationRules(name='default', rules=dict(DEFAULT_RULES))
    db.add(row); db.commit(); db.refresh(row)
    return row


def update_rules(db: Session, payload: dict) -> AutomationRules:
    row = get_rules(db)
    rules = dict(DEFAULT_RULES)
    rules.update(payload or {})
    rules['positive_review_min_rating'] = max(1, min(5, int(rules.get('positive_review_min_rating', 5))))
    rules['max_auto_answer_chars'] = max(200, min(3000, int(rules.get('max_auto_answer_chars', 900))))
    rules['autopublish_max_per_run'] = max(1, min(100, int(rules.get('autopublish_max_per_run', 10))))
    rules['autopublish_interval_seconds'] = max(60, min(86400, int(rules.get('autopublish_interval_seconds', 900))))
    rules['autopublish_pause_between_items_seconds'] = max(1, min(300, int(rules.get('autopublish_pause_between_items_seconds', 8))))
    matrix = rules.get('autopublish_matrix') if isinstance(rules.get('autopublish_matrix'), dict) else {}
    normalized_matrix = {}
    for platform in ['WB', 'OZON', 'YM']:
        raw = matrix.get(platform) or matrix.get(platform.lower()) or {}
        normalized_matrix[platform] = {'reviews': bool(raw.get('reviews')), 'questions': bool(raw.get('questions'))}
    # Backward compatibility: old switches fill the new matrix if the matrix was absent.
    if not isinstance(payload.get('autopublish_matrix') if isinstance(payload, dict) else None, dict):
        if rules.get('autopublish_positive_reviews'):
            normalized_matrix['WB']['reviews'] = True
        if rules.get('autopublish_questions'):
            normalized_matrix['WB']['questions'] = True
    rules['autopublish_matrix'] = normalized_matrix
    rules['ai_generation_enabled'] = bool(rules.get('ai_generation_enabled', True))
    rules['ai_fallback_to_local_templates'] = bool(rules.get('ai_fallback_to_local_templates', True))
    rules['autopublish_local_templates'] = bool(rules.get('autopublish_local_templates', True))
    for key in ['require_review_categories','require_review_risk_levels','forbidden_phrases','signatures','expanded_review_categories']:
        if not isinstance(rules.get(key), list):
            rules[key] = []
    row.rules = rules
    db.commit(); db.refresh(row)
    return row


def apply_publication_rules(result: dict, item_type: str, rating: int | None, db: Session | None = None) -> dict:
    if not db:
        return result
    rules = get_rules(db).rules or DEFAULT_RULES
    text = (result.get('answer_text') or '').lower()
    category = (result.get('category') or '').strip().lower()
    risk = (result.get('risk_level') or 'medium').strip().lower()

    forbidden = [str(x).lower() for x in rules.get('forbidden_phrases', [])]
    required_categories = [str(x).lower() for x in rules.get('require_review_categories', [])]
    required_risks = [str(x).lower() for x in rules.get('require_review_risk_levels', [])]

    can = bool(result.get('can_autopublish'))
    reasons = []

    platform = str(result.get('platform') or result.get('marketplace') or '').upper()
    matrix = rules.get('autopublish_matrix') or {}
    platform_rules = matrix.get(platform) or {}

    if item_type == 'review':
        if rating is None or rating < int(rules.get('positive_review_min_rating', 5)):
            can = False; reasons.append('Оценка ниже минимальной для автопубликации.')
        if category in required_categories:
            can = False; reasons.append('Категория требует ручной проверки по правилам.')
    elif item_type == 'question':
        pass

    if risk in required_risks:
        can = False; reasons.append('Уровень риска требует ручной проверки.')
    if len(result.get('answer_text') or '') > int(rules.get('max_auto_answer_chars', 900)):
        can = False; reasons.append('Ответ длиннее разрешенного лимита.')
    if any(phrase and phrase in text for phrase in forbidden):
        can = False; reasons.append('В ответе есть запрещенная фраза.')

    result['can_autopublish'] = can
    if reasons:
        base_reason = result.get('reason') or ''
        result['reason'] = (base_reason + ' ' + ' '.join(reasons)).strip()
    return result
