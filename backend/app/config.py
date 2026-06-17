from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_login: str = "admin"
    app_password: str = "change-me"

    database_url: str = "sqlite:///./cxhub.db"

    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    # WB: поддерживаем оба названия из Render
    wb_api_token: str = Field(
        default="",
        validation_alias=AliasChoices("WB_API_TOKEN", "WB_API_KEY", "wb_api_token", "wb_api_key"),
    )

    # Ozon: нужны два разных значения
    ozon_sync_enabled: bool = False
    ozon_client_id: str = ""
    ozon_api_key: str = ""

    ozon_sync_take: int = 100
    ozon_request_timeout_seconds: float = 30
    ozon_request_pause_seconds: float = 2

    ozon_auto_sync_enabled: bool = False
    ozon_auto_sync_interval_seconds: int = 900
    ozon_auto_sync_initial_delay_seconds: int = 40
    ozon_sync_pages_per_block_run: int = 5

    # Slot Hunter notifications
    telegram_bot_token: str = Field(default="", validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN", "telegram_bot_token"))
    telegram_chat_id: str = Field(default="", validation_alias=AliasChoices("TELEGRAM_CHAT_ID", "TG_CHAT_ID", "telegram_chat_id"))
    wb_booking_auto_check_interval_seconds: int = 900
    wb_booking_notify_empty_checks: bool = True

    wb_sync_mode: str = "both"
    wb_sync_unanswered_only: bool | None = None
    wb_sync_take: int = 100
    wb_sync_max_pages: int = 20
    wb_sync_pages_per_block_run: int = 1

    wb_retry_attempts: int = 1
    wb_retry_base_delay_seconds: float = 15
    wb_request_pause_seconds: float = 12
    wb_global_min_request_interval_seconds: float = 12
    wb_global_429_circuit_breaker_seconds: int = 3600
    wb_request_timeout_seconds: float = 20
    wb_sync_max_runtime_seconds: int = 900

    wb_auto_sync_enabled: bool = True
    wb_auto_sync_interval_seconds: int = 900
    wb_auto_sync_initial_delay_seconds: int = 20
    wb_auto_sync_strategy: str = "sweep_all_blocks"

    wb_operational_sync_enabled: bool = True
    wb_operational_sync_interval_seconds: int = 600
    wb_backfill_sync_enabled: bool = True
    wb_backfill_sync_interval_seconds: int = 1800
    wb_backfill_initial_delay_seconds: int = 180
    wb_rate_limit_cooldown_seconds: int = 1800

    enable_marketplace_publishing: bool = False
    wb_diagnostic_counts_enabled: bool = False
    wb_feedback_count_diagnostics_enabled: bool = False
    ai_auto_classify_on_sync: bool = True

    def effective_wb_sync_mode(self) -> str:
        mode = (self.wb_sync_mode or "").strip().lower()
        if mode in {"answered", "unanswered", "both"}:
            return mode
        if self.wb_sync_unanswered_only is True:
            return "unanswered"
        if self.wb_sync_unanswered_only is False:
            return "answered"
        return "both"


settings = Settings()