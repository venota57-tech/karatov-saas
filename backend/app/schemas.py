from datetime import datetime
from pydantic import BaseModel, ConfigDict

class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    platform: str
    external_id: str
    sku: str | None = None
    product_name: str | None = None
    product_url: str | None = None
    rating: int | None = None
    text: str | None = None
    pros: str | None = None
    cons: str | None = None
    client_name: str | None = None
    created_at_marketplace: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    has_answer: bool
    ai_category: str | None = None
    ai_sentiment: str | None = None
    ai_risk_level: str | None = None
    ai_can_autopublish: bool
    ai_reason: str | None = None
    ai_tags: list[str] | None = None
    draft_answer: str | None = None
    final_answer: str | None = None
    status: str
    source_status: str | None = None
    operational_status: str | None = None
    last_seen_source: str | None = None
    publish_blocked_reason: str | None = None
    response_origin: str | None = None
    no_text_rating: bool | None = None
    response_allowed: bool | None = None
    source_status: str | None = None
    operational_status: str | None = None
    last_seen_source: str | None = None
    publish_blocked_reason: str | None = None
    response_origin: str | None = None

class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    platform: str
    external_id: str
    sku: str | None = None
    product_name: str | None = None
    product_url: str | None = None
    text: str | None = None
    client_name: str | None = None
    created_at_marketplace: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    has_answer: bool
    ai_category: str | None = None
    ai_risk_level: str | None = None
    ai_can_autopublish: bool
    ai_reason: str | None = None
    ai_tags: list[str] | None = None
    draft_answer: str | None = None
    final_answer: str | None = None
    status: str

    source_status: str | None = None
    operational_status: str | None = None
    last_seen_source: str | None = None
    publish_blocked_reason: str | None = None
    response_origin: str | None = None

class AnswerUpdate(BaseModel):
    final_answer: str

class SyncResult(BaseModel):
    platform: str
    imported_reviews: int = 0
    imported_questions: int = 0
    message: str

class AutomationRulesPayload(BaseModel):
    autopublish_positive_reviews: bool = False
    positive_review_min_rating: int = 5
    autopublish_questions: bool = False
    require_review_categories: list[str] = ['проба/маркировка', 'камень/вставка', 'замок/застежка', 'качество/брак', 'размер']
    require_review_risk_levels: list[str] = ['medium', 'high']
    forbidden_phrases: list[str] = ['обменяем', 'гарантируем возврат', 'подделка']
    max_auto_answer_chars: int = 900
    real_autopublish_enabled: bool = False
    auto_generate_on_sync: bool = True
    autopublish_local_templates: bool = True
    autopublish_max_per_run: int = 10
    autopublish_interval_seconds: int = 900
    autopublish_pause_between_items_seconds: int = 8
    ai_generation_enabled: bool = True
    ai_fallback_to_local_templates: bool = True
    autopublish_matrix: dict = {
        'WB': {'reviews': False, 'questions': False},
        'OZON': {'reviews': False, 'questions': False},
        'YM': {'reviews': False, 'questions': False},
    }
    custom_system_prompt: str = ''
    review_prompt_template: str = ''
    question_prompt_template: str = ''
    template_rules_text: str = ''
    local_templates_text: str = ''
    signatures: list[str] = ['С уважением, команда KARATOV']
    expanded_review_categories: list[str] = []
    category_keywords_text: str = ''

class AutomationRulesOut(AutomationRulesPayload):
    updated_at: datetime | None = None
