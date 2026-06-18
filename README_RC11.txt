Release 1.0 RC1.1 — WB Sync Scheduler 2.0

Главное изменение:
- убран глобальный стоп всего WB после 429;
- 429 ставит на паузу только конкретный блок;
- один тик = один WB-блок = одна страница;
- backfill сохраняет курсоры по страницам;
- WB questions получают отдельную очередь;
- /sync/status показывает health_summary по каждому WB-блоку.

Рекомендуемые Render env на старт:
WB_SYNC_MODE=both
WB_SYNC_TAKE=50
WB_SYNC_MAX_PAGES=100000
WB_SYNC_PAGES_PER_BLOCK_RUN=1
WB_AUTO_SYNC_INTERVAL_SECONDS=120
WB_REQUEST_PAUSE_SECONDS=2
WB_GLOBAL_MIN_REQUEST_INTERVAL_SECONDS=2
WB_RATE_LIMIT_COOLDOWN_SECONDS=120

После деплоя:
1. /system/migrate
2. /sync/status
3. POST /sync/wb/next несколько раз или ждать автоцикл.
