# KARATOV CX Hub — Changelog

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
