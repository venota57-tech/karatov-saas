from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_login: str = 'admin'
    app_password: str = 'change-me'
    database_url: str = 'sqlite:///./cxhub.db'
    openai_api_key: str = ''
    openai_model: str = 'gpt-4.1-mini'
    wb_api_token: str = ''
    ozon_sync_enabled: bool = False
    ozon_client_id: str = ''
    ozon_api_key: str = ''
    ozon_sync_take: int = 100
    ozon_request_timeout_seconds: float = 30
    ozon_request_pause_seconds: float = 2

    # Ozon auto sync/backfill
    ozon_auto_sync_enabled: bool = False
    ozon_auto_sync_interval_seconds: int = 900
    ozon_auto_sync_initial_delay_seconds: int = 40
    # unanswered = только без ответа, answered = только с ответом, both = оба потока
    wb_sync_mode: str = 'both'
    # старый флаг оставлен для совместимости со старым .env
    wb_sync_unanswered_only: bool | None = None
    wb_sync_take: int = 100
    wb_sync_max_pages: int = 20
    # v2.3: how many API pages to request in a single block run. Keep low to avoid WB global limiter.
    wb_sync_pages_per_block_run: int = 1
    wb_retry_attempts: int = 1
    wb_retry_base_delay_seconds: float = 15
    wb_request_pause_seconds: float = 12
    # v3.8: one global WB API gate for all sync and publishing requests.
    # This is intentionally conservative: one request queue + long circuit breaker after any 429.
    wb_global_min_request_interval_seconds: float = 12
    wb_global_429_circuit_breaker_seconds: int = 3600
    wb_request_timeout_seconds: float = 20
    wb_sync_max_runtime_seconds: int = 900
    wb_auto_sync_enabled: bool = False
    wb_auto_sync_interval_seconds: int = 900
    wb_auto_sync_initial_delay_seconds: int = 20
    wb_auto_sync_strategy: str = 'dual_drip'
    # v2.4: two independent loops. Operational queues refresh often; historical archive/questions backfill slowly.
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

    # FBO autobooking notifications
    telegram_bot_token: str = ''
    telegram_chat_id: str = ''
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_user: str = ''
    smtp_password: str = ''
    smtp_use_tls: bool = True
    email_from: str = ''
    email_to: str = ''
    fbo_booking_state_path: str = '/app/fbo_booking_state.json'

    def effective_wb_sync_mode(self) -> str:
        mode = (self.wb_sync_mode or '').strip().lower()
        if mode in {'answered', 'unanswered', 'both'}:
            return mode
        if self.wb_sync_unanswered_only is True:
            return 'unanswered'
        if self.wb_sync_unanswered_only is False:
            return 'answered'
        return 'both'

settings = Settings()
