# RC1.2 Stabilization Release

## Цель релиза

RC1.2 исправляет ключевые проблемы RC1.1:

1. WB global limiter всё еще блокирует следующие блоки после 429.
2. WB Questions и WB Archive не доходят до запуска.
3. Ozon может крутить один и тот же диапазон 500/1000 записей.
4. Нет пользовательской документации и changelog.

## Что должно быть исправлено

### WB Scheduler

- 429 одного WB-блока не должен останавливать остальные блоки.
- feedbacks_unanswered, feedbacks_answered, feedbacks_archive, questions_unanswered, questions_answered должны иметь независимый статус.
- Статусы блоков должны быть видны в /sync/status.

### Ozon Cursor

- Cursor / last_id должен сохраняться по каждому блоку.
- Ozon не должен каждый раз начинать с первой страницы.
- /sync/ozon/status должен показывать blocks и cursors.
- last_finished_at должен заполняться всегда.

### Документация

- Добавить USER_GUIDE.md.
- Добавить CHANGELOG.md.
- Обновлять документацию при каждом следующем релизе.
