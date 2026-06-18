
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
