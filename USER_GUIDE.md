

## Release 1.0 RC1.4 — Product Integrity

В этом релизе рабочие разделы должны показывать полный загруженный объем данных, без искусственных потолков на 500/1000/2000 записей.

Исключения: короткие summary-блоки, топы, примеры, превью карточек и последние события — это осознанные витрины, а не источник данных.

После деплоя проверить:
- `/health`
- `/ops/product-summary?platform=ALL`
- `/sync/status`
- `/sync/ozon/status`

Если Product Summary возвращает `items`, Quality Hub должен заполниться товарами.

# KARATOV CX Hub — инструкция пользователя

## Назначение сервиса

KARATOV CX Hub — операционная платформа для работы с маркетплейсами Wildberries и Ozon.

Сервис помогает:
- обрабатывать отзывы;
- обрабатывать вопросы;
- контролировать архив ответов;
- отслеживать синхронизации;
- анализировать клиентский опыт;
- выявлять проблемные товары;
- готовить данные для Quality Hub и Operations Hub.

## Основные разделы

### Отзывы без ответа

Показывает отзывы, по которым еще нет ответа.

Используется для:
- ручной обработки;
- AI-генерации ответа;
- контроля SLA;
- подготовки к автопубликации.

### Отзывы с ответом / архив

Показывает отзывы, по которым ответ уже есть.

Используется для:
- контроля качества ответов;
- анализа истории;
- поиска повторяющихся проблем;
- проверки архива WB/Ozon.

### Вопросы без ответа

Показывает вопросы покупателей, которые требуют ответа.

Используется для:
- оперативного ответа;
- контроля SLA;
- подготовки базы знаний.

### Вопросы с ответом

Показывает историю обработанных вопросов.

Используется для:
- анализа частых вопросов;
- контроля архива;
- обучения AI.

### AI Summary

Сводка по отзывам и вопросам.

Используется для:
- выявления частых жалоб;
- поиска проблемных артикулов;
- анализа преимуществ товара;
- подготовки задач для производства, технологов и контента.

### Аномалии рейтинга

Показывает товары с подозрительной или негативной динамикой рейтинга.

Используется для:
- раннего обнаружения проблем;
- приоритизации задач;
- контроля качества товара.

### Товары и рейтинги

Показывает товары, рейтинги, количество отзывов и ссылки на карточки.

Используется для:
- контроля ассортимента;
- анализа карточек;
- перехода к товару на маркетплейсе.

### Slot Hunter PRO

Модуль для поиска окон поставок FBO Wildberries.

Используется для:
- мониторинга слотов;
- контроля складов;
- настройки коэффициентов;
- подготовки к автоматическому бронированию.

## Проверка после деплоя

После каждого релиза нужно проверить:

- /health
- /sync/status
- /sync/wb/status
- /sync/ozon/status

## Норма работы

Сервис работает корректно, если:

- last_error = null или ошибка локализована в конкретном блоке;
- last_finished_at обновляется;
- WB Reviews обновляются;
- WB Questions запускаются;
- WB Archive пополняется;
- Ozon не крутит один и тот же диапазон записей;
- статусы блоков отображаются отдельно.

## Известные ограничения

На этапе RC1.2:

- Яндекс Маркет еще не подключен полностью;
- Operations Hub находится в разработке;
- автопубликация требует дополнительного quality gate;
- Slot Hunter PRO требует сохранения настроек между релизами.

## RC1.6.1 seller cabinet answers
Если оператор ответил на отзыв или вопрос напрямую в кабинете WB/Ozon, CX Hub может подтянуть этот ответ через answered/archive sync и backfill.

Manual run:
`/sync/backfill-marketplace-answers`

После backfill записи с ответом получают:
- `has_answer=true`
- `response_origin=seller_cabinet`
- `final_answer`
- `status=answered_on_marketplace`, если запись пришла из answered/archive

`answered_at` заполняется только если маркетплейс передал дату ответа в raw payload.

## RC1.6.2 dashboard and operations sync
Главная панель и левое меню используют `/system/dashboard` как источник счетчиков.
Если отдельный API временно вернул пустой ответ, интерфейс сохраняет последние известные ненулевые счетчики.

Операции:
- `/operations/sync?platform=ALL` запускает синхронизацию в фоне и быстро возвращает статус старта.
- `/operations/sync/status` показывает running, elapsed_seconds, last_success_at, last_error и result.
- Неподключенные типы документов показываются как `not_connected`, без демо-данных.

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
