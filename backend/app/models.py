from datetime import datetime
from sqlalchemy import String, Text, DateTime, Integer, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


def wb_product_url(sku: str | None) -> str | None:
    if not sku:
        return None
    value = str(sku).strip()
    if not value or not value.isdigit():
        return None
    return f"https://www.wildberries.ru/catalog/{value}/detail.aspx"


def _walk_for_product_id(obj):
    keys = {'nmId', 'nmID', 'nm_id', 'nmid'}
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value not in (None, ''):
                return str(value)
        for key in ('productDetails', 'product', 'nomenclature', 'nm'):
            found = _walk_for_product_id(obj.get(key))
            if found:
                return found
        for value in obj.values():
            found = _walk_for_product_id(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _walk_for_product_id(value)
            if found:
                return found
    return None


def wb_product_url_from_raw(raw: dict | None, sku: str | None = None) -> str | None:
    url = wb_product_url(sku)
    if url:
        return url
    nm_id = _walk_for_product_id(raw)
    return wb_product_url(nm_id)



def ozon_product_url(raw: dict | None, sku: str | None = None, product_name: str | None = None) -> str | None:
    # Ozon product URLs are not stable from seller API in every response.
    # Prefer direct URL if API returned it; then product_id/sku search fallback.
    direct = _walk_for_product_id(raw) if False else None
    if isinstance(raw, dict):
        for key in ('url', 'product_url', 'productUrl', 'link'):
            if raw.get(key):
                return str(raw[key])
    value = (sku or product_name or '').strip() if isinstance(sku or product_name, str) else str(sku or product_name or '').strip()
    if not value:
        return None
    return f"https://www.ozon.ru/search/?text={value}"

class Review(Base):
    __tablename__ = 'reviews'
    __table_args__ = (UniqueConstraint('platform', 'external_id', name='uq_review_platform_external'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    pros: Mapped[str | None] = mapped_column(Text, nullable=True)
    cons: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at_marketplace: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answered_at = mapped_column(DateTime, nullable=True)
    has_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Marketplace sync state. Historical data can be used for analytics,
    # but the operational queue must only show items WB returned as unanswered in the latest successful sync.
    source_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    operational_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_seen_source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_seen_sync_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    publish_blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_origin: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    ai_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_can_autopublish: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    draft_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default='new', index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def product_url(self) -> str | None:
        return wb_product_url_from_raw(self.raw, self.sku) if self.platform == 'WB' else (ozon_product_url(self.raw, self.sku, self.product_name) if self.platform == 'OZON' else None)

class Question(Base):
    __tablename__ = 'questions'
    __table_args__ = (UniqueConstraint('platform', 'external_id', name='uq_question_platform_external'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at_marketplace: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answered_at = mapped_column(DateTime, nullable=True)
    has_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Marketplace sync state. Historical data can be used for analytics,
    # but the operational queue must only show items WB returned as unanswered in the latest successful sync.
    source_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    operational_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_seen_source: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_seen_sync_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    publish_blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_origin: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    ai_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_risk_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_can_autopublish: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    draft_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default='new', index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def product_url(self) -> str | None:
        return wb_product_url_from_raw(self.raw, self.sku) if self.platform == 'WB' else (ozon_product_url(self.raw, self.sku, self.product_name) if self.platform == 'OZON' else None)

class RatingSnapshot(Base):
    __tablename__ = 'rating_snapshots'
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    sku: Mapped[str] = mapped_column(String(128), index=True)
    product_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rating: Mapped[str | None] = mapped_column(String(32), nullable=True)
    feedbacks_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    @property
    def product_url(self) -> str | None:
        return wb_product_url_from_raw(self.raw, self.sku) if self.platform == 'WB' else (ozon_product_url(self.raw, self.sku, self.product_name) if self.platform == 'OZON' else None)

class AutomationRules(Base):
    __tablename__ = 'automation_rules'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default='default', unique=True, index=True)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class MarketplaceOperation(Base):
    __tablename__ = 'marketplace_operations'
    __table_args__ = (UniqueConstraint('platform', 'operation_type', 'external_id', name='uq_operation_platform_type_external'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    operation_type: Mapped[str] = mapped_column(String(64), index=True)  # return, act, shortage, surplus, anonymization, discrepancy
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    document_number: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    warehouse: Mapped[str | None] = mapped_column(String(256), nullable=True)
    amount: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default='new', index=True)
    responsible: Mapped[str | None] = mapped_column(String(128), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
