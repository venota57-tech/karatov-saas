
## Release 1.0 RC1.4 — Product Integrity Release

### Исправлено

- Убраны искусственные лимиты из рабочих списков отзывов, вопросов и Operations Hub.
- Убраны frontend-лимиты `/reviews?limit=2000`, `/questions?limit=2000`, `/operations?limit=500`, `/ops/product-summary?limit=500`.
- Product Summary восстановлен: каталог строится по всем загруженным отзывам и вопросам, snapshots используются как дополнительное обогащение.
- Quality Hub снова должен получать товары из Product Summary.
- Подтягивание ответов из кабинетов WB/Ozon зафиксировано как обязательная часть answered/archive blocks: final_answer + response_origin=seller_cabinet, без перетирания manual_app/auto_app.
- Summary/top/preview ограничения оставлены только там, где это осознанное UI-превью.

### Важно

- Автосинк WB/Ozon нужно включать после успешного деплоя и проверки `/health`, `/ops/product-summary`, `/sync/status`, `/sync/ozon/status`.


## Release 1.0 RC1.3.3

### Исправлено

- Убраны искусственные лимиты из рабочих списков отзывов.
- Убраны искусственные лимиты из рабочих списков вопросов.
- Убраны искусственные лимиты из Operations Hub / возвратов / актов.
- Убраны искусственные лимиты из Product Summary / каталога товаров.
- Frontend больше не запрашивает `/reviews?limit=2000`, `/questions?limit=2000`, `/operations?limit=500`, `/ops/product-summary?limit=500`.

### Не изменялось

- Ограничения на топы, примеры, превью карточек и историю публикаций оставлены, так как это UI-превью, а не потолок данных.


## Release 1.0 RC1.3.2

### Исправлено

- Убран backend hard cap 1000 для `/reviews`.
- Control Tower теперь берет счетчики отзывов, вопросов и очереди ответа из `/summary`, а не из длины загруженного массива.
- AI Summary больше не должен показывать искусственный потолок 1000 отзывов / 1135 коммуникаций.

\n## Release 1.0 RC1.2.1\n\n### Исправлено\n\n- Изменен порядок WB sweep: questions_unanswered теперь запускается сразу после feedbacks_unanswered.\n- Тяжелые исторические блоки feedbacks_answered, questions_answered и feedbacks_archive перенесены после операционных очередей.\n- Это нужно, чтобы WB Questions не оставались never_run из-за 429 на архивных endpoint.\n\n# KARATOV CX Hub — Changelog

## Release 1.0 RC1.2 — technical patch

### Исправлено

- Убран глобальный WB 429 circuit breaker из WB-клиента.
- Сохранен общий WB request gate, чтобы не было параллельных запросов.
- 429 WB теперь возвращается как ошибка текущего запроса и должен обрабатываться scheduler как cooldown конкретного блока.
- Ozon cursor больше не удаляется автоматически после достижения конца диапазона.
- В Ozon result добавлены cursor_key, start_last_id, finish_last_id и end_reached.
- Ozon auto loop теперь всегда заполняет last_finished_at при успехе и ошибке.


## Release 1.0 RC1.2

Тип релиза: Stabilization Release.

### Исправления

- Зафиксирована задача убрать глобальный WB 429 cooldown.
- Зафиксирована задача перевести WB Scheduler на per-block cooldown.
- Зафиксирована задача восстановить независимый запуск WB Questions и WB Archive.
- Зафиксирована задача исправить Ozon cursor/backfill.
- Зафиксирована задача не допускать повторной прокрутки одних и тех же 500/1000 записей Ozon.

### Документация

- Добавлен USER_GUIDE.md.
- Добавлен CHANGELOG.md.
- Добавлен RC12_NOTES.md.

## Release 1.0 RC1.1

### Изменения

- Добавлен WB Scheduler 2.0.
- Добавлена попытка независимой обработки WB-блоков.
- Добавлены статусы blocks_state.
- Добавлены настройки Ozon pages_per_block_run.

### Известные проблемы

- WB global limiter продолжает блокировать следующие блоки после 429.
- WB Questions и WB Archive могут оставаться never_run.
- Ozon может не завершать run и не писать last_finished_at.
- Ozon может повторно читать один и тот же диапазон записей.

## RC1.6.1 Data Integrity & Product Limits
- Added `/sync/backfill-marketplace-answers` to backfill answers published directly in WB/Ozon seller cabinets from already synced answered/archive raw data.
- Product Summary / Quality Hub no longer use backend `500` as source-of-truth limit. Endpoint now supports `total`, `offset`, `limit`; `limit=0` returns all product groups.
- Operations list now uses server-side pagination with `total`, `offset`, `limit`, `items`.
- Ozon operations request limit increased from 100 to 1000 where safe.
- Operations diagnostics now explicitly mark `shortage`, `surplus`, `anonymization`, `discrepancy` as `not_connected` until endpoint/permission is implemented.

## RC1.6.2 Stability + Marketplace Parity
- Added `/system/dashboard` as the server-side source of truth for Control Tower and sidebar counters.
- Frontend now preserves last known non-zero counters if diagnostics/dashboard temporarily returns an empty fallback.
- Operations sync is converted to non-blocking start/status flow; `/operations/sync` now starts a background run instead of holding the UI request.
- Added `/operations/sync/status` and `/operations/sync/start`.
- Operations list is paginated with `total`, `offset`, `limit`, `items`.
- Product Summary / Quality Hub backend source caps removed where `500` was acting as source-of-truth limit.
- Unsupported Operations document types are explicitly marked `not_connected`; fake rows remain forbidden.

## RC1.6.5 — Web/Worker Foundation
- Removed background sync loops from FastAPI lifespan to protect web responsiveness.
- Added Redis-backed job queue foundation and worker entrypoint.
- Added SyncJob, SyncCursor and DashboardSnapshot models.
- Rebuilt /system/dashboard as real fast SQL counters.
- Kept /system/diagnostics lightweight.
- Added frontend request timeouts and limited reviews/questions loading.

## RC1.6.5 Data Restore
- Restored priority /reviews and /questions routes before included routers.
- Fixed dashboard product count to use real reviews/questions product keys.
- Raised Product Summary frontend load from 500 to 5000 for current full catalog visibility.
- Fixed startup dashboard request that referenced requestedPlatform outside refreshAll.

## RC1.6.5 Data Restore - no false ceilings
- Restored priority reviews/questions endpoints with correct platform=ALL behavior.
- Product Summary UI now loads by server-reported total instead of fixed 500/5000 ceilings.
- Dashboard product count now uses the same reviews/questions source as Product Summary.
- Removed stale dashboard-count preservation when switching marketplace tabs.

## RC1.7.0 Marketplace OS Business Parity
- Unified dashboard, product totals, operator work queue, Quality Hub and Operations summary around server-side business totals.
- Removed false business ceilings: UI pagination is allowed, but totals must come from server aggregation.
- Added Marketplace OS API endpoints: /marketplace-os/dashboard, /marketplace-os/work-queue, /marketplace-os/quality, /marketplace-os/operations.
- Added seller-cabinet published answer enrichment service for WB and Ozon.
- Added worker job types: wb_answer_enrichment, ozon_answer_enrichment, answer_enrichment_all, marketplace_os_refresh.
- Preserved HTTP-first web startup: no WB/Ozon/autopublish/booking/dashboard loops in FastAPI lifespan.

## RC1.7.1 Full Sync Engine
- Added worker-first full sync orchestration for WB, Ozon, Operations and published-answer enrichment.
- Ozon review cursors are restored from and persisted to SyncCursor so backfill does not restart after deploys.
- WB full sync runs all communication/archive blocks in worker cycles without putting work in web startup.
- Operations sync no longer writes new as a meaningless default for freshly synced rows; unsupported blocks are reported honestly.
- Added /full-sync/enqueue and /full-sync/plan.
- Added Render worker blueprint entry: karatov-saas-worker, command python -m app.worker.

## RC1.7.2 Free Cron-Pulse Sync Engine
- Replaced paid Render Background Worker requirement with /cron/tick free cron-pulse mode.
- Added CRON_SECRET-protected endpoints: /cron/tick, /cron/status, /cron/wake.
- One external cron hit runs one safe sync block: Ozon, WB, published-answer enrichment or Operations.
- Ozon review cursors are restored from and saved to SyncCursor so deploys do not restart backfill from the first 1000.
- Operations rows now separate marketplace_status/cx_workflow_status and stop using meaningless status=new for synced rows.
- Render blueprint no longer requires paid worker service.

## RC1.7.3 GitHub Actions Sync Runner
- Replaced unstable long web cron execution with a scheduled GitHub Actions sync runner.
- Added .github/workflows/marketplace-sync.yml and backend/app/github_sync_runner.py.
- Ozon reviews now use two passes: latest page for fresh data and persisted backfill cursor for archive/backlog beyond the old 1000-row wall.
- WB answered/archive page cursors are persisted through SyncCursor across separate GitHub Actions runs.
- Published answers enrichment is run as part of the scheduled runner.
- Operations sync is included in the scheduled runner; meaningless operation status=new is migrated to synced/new_to_review.
- Added /sync-runner/status and /sync-runner/sla with response-speed analytics for reviews and questions.

## RC1.7.3 Runner DB Resilience
- GitHub Actions sync runner now uses short-lived DB sessions per Ozon page/WB block/Operations/Answers stage.
- External Render Postgres SSL drops are retried with engine.dispose and fresh sessions.
- Failed runner status is persisted through a fresh DB transaction to avoid PendingRollback masking the real error.
- Ozon backfill remains durable through SyncCursor and continues across runs.

## RC1.7.4 Sync Runner Stable Ingestion
- GitHub runner now isolates stages: Ozon, WB, Operations, Answers and Analytics no longer fail the whole run together.
- Runner uses short-lived DB sessions per Ozon page and WB block to survive External Render Postgres SSL disconnects.
- Ozon page size is reduced for GitHub runner only; this is a technical chunk size, not a business ceiling.
- Ozon latest pass and persisted backfill pass continue through SyncCursor.
- Failed stages are written to SyncJob without PendingRollback masking the real cause.

## RC1.7.6 Split Cadence Marketplace Sync
- Accepted split-cadence strategy: hot every 5 minutes, answers every 10 minutes, backfill every 30 minutes, operations every 30 minutes, nightly deep sync once per day.
- Monolithic marketplace-sync workflow is manual-only to reduce API/DB pressure.
- Runner supports kind=hot, answers, backfill, operations, analytics and all.
- Fresh marketplace data is prioritized: WB unanswered feedbacks/questions and Ozon latest review/question pages run on the lowest free interval.
- Archive and operations are isolated from fresh sync so they cannot block operator-facing data.
