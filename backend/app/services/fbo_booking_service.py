import asyncio
import json
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

from ..config import settings

STATE_PATH = Path(os.getenv('FBO_BOOKING_STATE_PATH', '/app/fbo_booking_state.json'))
TELEGRAM_SESSION_DIR = Path(os.getenv('TELEGRAM_SESSION_DIR', '/app/telegram_sessions'))
TELEGRAM_WEB_PROFILE_DIR = Path(os.getenv('TELEGRAM_WEB_PROFILE_DIR', '/app/playwright_profiles/telegram_web_postavleno'))
TELEGRAM_WEB_SCREENSHOT = Path(os.getenv('TELEGRAM_WEB_SCREENSHOT', '/app/playwright_profiles/telegram_web_postavleno_last.png'))
POSTAVLENO_REQUEST_SCREENSHOT = Path(os.getenv('POSTAVLENO_REQUEST_SCREENSHOT', '/app/playwright_profiles/postavleno_request_last.png'))

# Runtime Telegram Web objects. They intentionally stay alive between API calls
# so the QR shown in the UI remains scannable and the same browser session is
# reused for POSTAVLENO actions.
_TG_WEB_PLAYWRIGHT = None
_TG_WEB_CONTEXT = None
_TG_WEB_PAGE = None
_TG_WEB_ALIAS = None

_LOGIN_MARKERS = [
    'Log in to Telegram', 'Войти в Telegram', 'Sign in to Telegram',
    'Scan QR', 'Сканируйте QR', 'Phone Number', 'Номер телефона',
    'Use Telegram on your phone', 'Пользуйтесь Telegram на телефоне',
    'Cloud password', 'Two-Step Verification', 'Password', 'Enter your password',
    'Облачный пароль', 'Двухэтапная аутентификация', 'Введите пароль',
]

async def _tg_web_get_page(alias: str | None = None, goto_home: bool = True):
    global _TG_WEB_PLAYWRIGHT, _TG_WEB_CONTEXT, _TG_WEB_PAGE, _TG_WEB_ALIAS
    from playwright.async_api import async_playwright
    safe_alias = alias or 'default'
    if _TG_WEB_CONTEXT is not None and _TG_WEB_PAGE is not None and _TG_WEB_ALIAS == safe_alias:
        try:
            if not _TG_WEB_PAGE.is_closed():
                if goto_home:
                    await _TG_WEB_PAGE.goto('https://web.telegram.org/a/', wait_until='domcontentloaded', timeout=90000)
                    await _TG_WEB_PAGE.wait_for_timeout(2500)
                return _TG_WEB_PAGE
        except Exception:
            pass
    try:
        if _TG_WEB_CONTEXT is not None:
            await _TG_WEB_CONTEXT.close()
    except Exception:
        pass
    try:
        if _TG_WEB_PLAYWRIGHT is not None:
            await _TG_WEB_PLAYWRIGHT.stop()
    except Exception:
        pass
    _TG_WEB_PLAYWRIGHT = await async_playwright().start()
    profile = _playwright_profile_dir(safe_alias)
    _TG_WEB_CONTEXT = await _TG_WEB_PLAYWRIGHT.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
        viewport={'width': 1365, 'height': 900},
        locale='ru-RU',
        args=['--no-sandbox', '--disable-dev-shm-usage'],
    )
    _TG_WEB_PAGE = _TG_WEB_CONTEXT.pages[0] if _TG_WEB_CONTEXT.pages else await _TG_WEB_CONTEXT.new_page()
    _TG_WEB_ALIAS = safe_alias
    if goto_home:
        await _TG_WEB_PAGE.goto('https://web.telegram.org/a/', wait_until='domcontentloaded', timeout=90000)
        await _TG_WEB_PAGE.wait_for_timeout(4500)
    return _TG_WEB_PAGE

async def _tg_web_save_screenshot(page) -> None:
    TELEGRAM_WEB_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(TELEGRAM_WEB_SCREENSHOT), full_page=True)

async def _tg_web_needs_2fa_password(page) -> tuple[bool, str]:
    """Detect Telegram Web cloud-password screen after QR login."""
    body_text = ''
    try:
        body_text = await page.locator('body').inner_text(timeout=8000)
    except Exception:
        body_text = ''
    try:
        if await page.locator('input[type="password"], input[autocomplete="current-password"]').count():
            return True, body_text
    except Exception:
        pass
    low = body_text.lower()
    markers = ['cloud password', 'two-step verification', 'enter your password', 'облачный пароль', 'двухэтап', 'введите пароль']
    return any(m in low for m in markers), body_text


async def _tg_web_fill_2fa_password(page, password: str) -> tuple[bool, str]:
    """Fill Telegram Web 2FA/cloud password and wait for the chat list to load."""
    if not password:
        return False, 'Пароль 2FA пустой'
    last_error = ''
    selectors = [
        'input[type="password"]',
        'input[autocomplete="current-password"]',
        'input[placeholder*="Password"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.click(timeout=5000)
                await loc.fill(password, timeout=5000)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(6500)
                logged, body = await _tg_web_is_logged_in(page)
                if logged:
                    return True, body
                needs, _ = await _tg_web_needs_2fa_password(page)
                if needs:
                    return False, 'Пароль введен, но Telegram Web всё еще просит облачный пароль. Возможно, пароль неверный.'
                return False, body[:800]
        except Exception as exc:
            last_error = str(exc)
    try:
        await page.keyboard.type(password, delay=25)
        await page.keyboard.press('Enter')
        await page.wait_for_timeout(6500)
        logged, body = await _tg_web_is_logged_in(page)
        return logged, body if logged else (body[:800] or last_error or 'Поле пароля не найдено')
    except Exception as exc:
        return False, str(exc)


async def _tg_web_is_logged_in(page) -> tuple[bool, str]:
    body_text = ''
    try:
        body_text = await page.locator('body').inner_text(timeout=12000)
    except Exception:
        body_text = ''
    if any(marker in body_text for marker in _LOGIN_MARKERS):
        return False, body_text
    checks = [
        'div[contenteditable="true"]',
        '[role="textbox"]',
        'input[placeholder*="Search"]',
        'input[placeholder*="Поиск"]',
        'a[href*="POSTAVLENO"]',
    ]
    for selector in checks:
        try:
            if await page.locator(selector).count():
                return True, body_text
        except Exception:
            pass
    logged = any(marker in body_text for marker in ['POSTAVLENO', 'Избранное', 'Saved Messages', 'Настройки', 'Settings'])
    return logged, body_text

async def _tg_web_open_bot(page, bot_username: str) -> str:
    username = bot_username.lstrip('@')
    await page.goto(f'https://web.telegram.org/a/#{username}', wait_until='domcontentloaded', timeout=90000)
    await page.wait_for_timeout(5000)
    logged, body = await _tg_web_is_logged_in(page)
    if not logged:
        return body
    if username.lower() in body.lower() or 'POSTAVLENO' in body:
        return body
    try:
        search = page.locator('input[placeholder*="Search"], input[placeholder*="Поиск"]').first
        if await search.count():
            await search.click(timeout=4000)
            await search.fill('@' + username, timeout=4000)
            await page.wait_for_timeout(2500)
            await page.get_by_text(re.compile(username, re.I)).first.click(timeout=6000)
            await page.wait_for_timeout(4500)
            body = await page.locator('body').inner_text(timeout=15000)
    except Exception:
        pass
    return body

async def close_telegram_web_runtime() -> None:
    global _TG_WEB_PLAYWRIGHT, _TG_WEB_CONTEXT, _TG_WEB_PAGE, _TG_WEB_ALIAS
    try:
        if _TG_WEB_CONTEXT is not None:
            await _TG_WEB_CONTEXT.close()
    except Exception:
        pass
    try:
        if _TG_WEB_PLAYWRIGHT is not None:
            await _TG_WEB_PLAYWRIGHT.stop()
    except Exception:
        pass
    _TG_WEB_PLAYWRIGHT = None
    _TG_WEB_CONTEXT = None
    _TG_WEB_PAGE = None
    _TG_WEB_ALIAS = None

DEFAULT_SETTINGS: dict[str, Any] = {
    'enabled': True,
    'warehouses': ['Коледино', 'Электросталь'],
    'supply_type': 'Суперсейф',
    'max_coefficient': 20,
    'start_date': '2026-05-21',
    'step_working_days': 3,
    'planning_horizon_days': 30,
    'search_from': '09:00',
    'search_to': '21:00',
    'check_interval_seconds': 30,
    'not_found_notify_after_minutes': 120,
    'auto_book': True,
    'draft_mode': 'auto_create_then_latest',
    'booking_mode_note': 'Сначала пытаемся через WB API. Если публичного метода бронирования нет — нужен Playwright-адаптер ЛК WB.',
    'telegram_enabled': True,
    'email_enabled': True,
    'interface_notifications_enabled': True,
    'holidays_mode': 'manual_exclusions',
    'excluded_dates': [],
    'transport_holidays': [],
    'manual_target_dates': [],
    # Hybrid mode: POSTAVLENO bot is used as a radar, WB cabinet/API as final booking point.
    'search_source_mode': 'wb_first',  # wb_cabinet | postavleno_only | hybrid
    'postavleno_enabled': False,
    'postavleno_bot_username': '@POSTAVLENOru_BOT',
    'postavleno_max_active_requests': 5,
    'postavleno_account_label': 'Не подключен',
    'postavleno_session_alias': 'default',
    'postavleno_phone': '',
    'postavleno_telegram_api_id': '',
    'postavleno_telegram_api_hash': '',
    'postavleno_connection_status': 'not_connected',
    'postavleno_last_message': '',
    'postavleno_last_checked_at': None,
    'postavleno_delete_requests_supported': False,
    'postavleno_use_same_start_end_date': True,
    'wb_final_booking_source': 'cabinet_playwright',
    'postavleno_auto_manage_requests': True,
    'postavleno_request_template': 'Склад: {warehouse}; тип: {supply_type}; дата: {date}; коэффициент до {max_coefficient}х',
    'postavleno_last_request_sync_at': None,
    'postavleno_source_mode': 'telegram_web',
    'telegram_web_status': 'not_connected',
    'telegram_web_last_screenshot_at': None,
    'telegram_web_last_checked_at': None,
    'telegram_web_last_error': '',
    'telegram_web_login_url': 'https://web.telegram.org/a/',
    'wb_api_monitor_enabled': False,
    'wb_cabinet_enabled': True,
    'wb_cabinet_url': 'https://seller.wildberries.ru',
    'wb_cabinet_safe_mode': True,
    'wb_api_last_audit_at': None,
    'wb_api_last_monitor_at': None,
    'wb_api_status': 'not_checked',
    'wb_api_capabilities': [],
    'wb_api_last_error': '',
    'wb_cabinet_connection_status': 'not_connected',
    'wb_cabinet_profile_alias': 'default',
    'wb_cabinet_last_action': '',
    'wb_cabinet_last_screenshot': '',
    'scheduler_enabled': True,
    'scheduler_mode': 'wb_first_autopilot',
    'scheduler_last_started_at': None,
    'scheduler_last_finished_at': None,
    'scheduler_next_retry_at': None,
    'scheduler_last_not_found_notified_at': None,
    'wb_api_request_min_delay_seconds': 3,
    'wb_api_cache_coefficients_seconds': 900,
    'wb_api_429_backoff_seconds': 300,
    'wb_auto_booking_status': 'cabinet_safe_mode_until_wb_flow_mapped',
    'wb_cabinet_check_interval_seconds': 300,
    'wb_cabinet_error_backoff_seconds': 120,
    'wb_cabinet_known_urls': ['https://seller.wildberries.ru/supplies-management/all-supplies','https://seller.wildberries.ru/supplies-management/create-supply','https://seller.wildberries.ru/supplies-management','https://seller.wildberries.ru'],
    'wb_cabinet_real_booking_enabled': False,
    'wb_cabinet_booking_mode': 'test',  # test | real
    'wb_cabinet_template_quantity': 1000,
    'wb_cabinet_default_warehouse': 'Электросталь',
    'wb_cabinet_last_route_step': '',
    'wb_cabinet_monitor_last_at': None,
    'wb_cabinet_live_view_enabled': True,
    'wb_cabinet_route_step_mode': 'auto',
    'wb_cabinet_require_final_confirmation': True,
    'wb_cabinet_final_confirmation_text': '',
    'wb_cabinet_expected_company': 'ГОЛДСТАРТ ООО',
    'wb_cabinet_detected_company': '',
}


_STATUS_LABELS = {
    'planned': 'Запланировано',
    'searching': 'Ищем окно',
    'found': 'Найден слот',
    'booking': 'Бронируем',
    'booked': 'Забронировано',
    'not_found': 'Не найдено',
    'error': 'Ошибка',
    'skipped_holiday': 'Праздник / ТК не возит',
}

_lock = asyncio.Lock()
_runtime = {
    'running': False,
    'last_tick_at': None,
    'last_error': None,
    'last_message': None,
    'next_retry_at': None,
    'cycle_running': False,
}


# WB API is sensitive to bursts. These guards keep the FBO module from
# hammering common-api/supplies-api when the user clicks buttons repeatedly.
_WB_API_CALL_LOCK = asyncio.Lock()
_WB_API_LAST_CALL_AT: float = 0.0
_WB_API_CACHE: dict[str, dict[str, Any]] = {}
_WB_API_429_UNTIL: float = 0.0



def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


def _default_state() -> dict[str, Any]:
    return {
        'settings': DEFAULT_SETTINGS.copy(),
        'targets': [],
        'logs': [],
        'notifications': [],
        'last_generated_at': None,
    }


def _load_state_sync() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
            data.setdefault('settings', DEFAULT_SETTINGS.copy())
            data['settings'] = {**DEFAULT_SETTINGS, **(data.get('settings') or {})}
            data.setdefault('targets', [])
            data.setdefault('logs', [])
            data.setdefault('notifications', [])
            return data
    except Exception:
        pass
    return _default_state()


def _save_state_sync(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(STATE_PATH)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, '%Y-%m-%d').date()


def _parse_time(value: str) -> time:
    return datetime.strptime(value, '%H:%M').time()


def _add_working_days(start: date, days: int, allowed_weekdays: set[int] | None = None) -> date:
    allowed_weekdays = allowed_weekdays or {0, 1, 2, 3, 4}
    cur = start
    added = 0
    while added < days:
        cur += timedelta(days=1)
        if cur.weekday() in allowed_weekdays:
            added += 1
    return cur


def build_target_dates(cfg: dict[str, Any]) -> list[str]:
    start = _parse_date(cfg.get('start_date') or DEFAULT_SETTINGS['start_date'])
    horizon = int(cfg.get('planning_horizon_days') or 30)
    step = int(cfg.get('step_working_days') or 3)
    end = start + timedelta(days=horizon)
    excluded = set(cfg.get('excluded_dates') or []) | set(cfg.get('transport_holidays') or [])
    result = []
    cur = start
    while cur <= end:
        iso = cur.isoformat()
        if cur.weekday() < 5 and iso not in excluded:
            result.append(iso)
        cur = _add_working_days(cur, step)
    return result


def _make_target_row(target_date: str, manual: bool = False, message: str | None = None) -> dict[str, Any]:
    return {
        'date': target_date,
        'manual': manual,
        'status': 'planned',
        'status_label': _STATUS_LABELS['planned'],
        'warehouse': None,
        'slot_time': None,
        'coefficient': None,
        'draft_id': None,
        'last_checked_at': None,
        'attempts': 0,
        'message': message or ('Добавлена вручную' if manual else 'Ожидает поиска'),
    }


def _upsert_targets(state: dict[str, Any]) -> None:
    cfg = state['settings']
    generated = build_target_dates(cfg)
    manual_dates = sorted(set(cfg.get('manual_target_dates') or []))
    wanted = sorted(set(generated) | set(manual_dates))
    existing = {t.get('date'): t for t in state.get('targets', []) if t.get('date')}
    excluded_all = set(cfg.get('excluded_dates') or []) | set(cfg.get('transport_holidays') or [])
    next_targets = []
    for target_date in wanted:
        manual = target_date in manual_dates
        row = existing.get(target_date) or _make_target_row(target_date, manual=manual)
        row['manual'] = bool(row.get('manual') or manual)
        if target_date in excluded_all:
            row.update({'status': 'skipped_holiday', 'status_label': _STATUS_LABELS['skipped_holiday'], 'message': 'Исключено настройками праздников/ТК'})
        elif row.get('status') == 'skipped_holiday' and target_date not in excluded_all:
            row.update({'status': 'planned', 'status_label': _STATUS_LABELS['planned'], 'message': row.get('message') or 'Ожидает поиска'})
        row['status_label'] = _STATUS_LABELS.get(row.get('status'), row.get('status_label') or row.get('status'))
        next_targets.append(row)
    state['targets'] = sorted(next_targets, key=lambda x: x.get('date') or '')
    state['last_generated_at'] = _now_iso()
    _refresh_postavleno_queue(state)


def _refresh_postavleno_queue(state: dict[str, Any]) -> None:
    """Maintain POSTAVLENO queue with max 5 active requests.

    The external bot instruction says it allows up to 5 requests. We do not assume
    old requests can be deleted, so the safest policy is: fill only nearest open
    target dates up to the limit, keep the rest in queue, and advance when a date
    is booked/skipped.
    """
    cfg = state.get('settings') or {}
    max_active = max(1, int(cfg.get('postavleno_max_active_requests') or 5))
    if not cfg.get('postavleno_enabled'):
        for t in state.get('targets', []):
            t['postavleno_status'] = 'disabled'
            t['postavleno_status_label'] = 'POSTAVLENO выключен'
        return

    active_count = 0
    for t in sorted(state.get('targets', []), key=lambda x: x.get('date') or ''):
        if t.get('status') in {'booked', 'skipped_holiday'}:
            t['postavleno_status'] = 'closed'
            t['postavleno_status_label'] = 'Закрыто'
        elif active_count < max_active:
            t['postavleno_status'] = 'active_request'
            t['postavleno_status_label'] = 'Размещен в POSTAVLENO' if t.get('postavleno_submitted_at') else 'Активный запрос'
            active_count += 1
        else:
            t['postavleno_status'] = 'queued'
            t['postavleno_status_label'] = 'В очереди'


def _log(state: dict[str, Any], action: str, result: str, details: str = '', target_date: str | None = None, warehouse: str | None = None, draft_id: str | None = None) -> None:
    state.setdefault('logs', []).insert(0, {
        'created_at': _now_iso(),
        'action': action,
        'target_date': target_date,
        'warehouse': warehouse,
        'draft_id': draft_id,
        'result': result,
        'details': details,
    })
    state['logs'] = state['logs'][:300]


def _notify_in_app(state: dict[str, Any], title: str, body: str, level: str = 'info') -> None:
    state.setdefault('notifications', []).insert(0, {
        'created_at': _now_iso(),
        'title': title,
        'body': body,
        'level': level,
        'read': False,
    })
    state['notifications'] = state['notifications'][:100]


async def read_state() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        _save_state_sync(state)
        return state


async def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        clean = {**state.get('settings', {}), **payload}
        # Normalize common fields from frontend.
        clean['warehouses'] = [x for x in clean.get('warehouses', []) if x]
        clean['max_coefficient'] = int(clean.get('max_coefficient') or 20)
        clean['step_working_days'] = int(clean.get('step_working_days') or 3)
        clean['planning_horizon_days'] = int(clean.get('planning_horizon_days') or 30)
        clean['check_interval_seconds'] = max(10, int(clean.get('check_interval_seconds') or 30))
        state['settings'] = clean
        _upsert_targets(state)
        _log(state, 'Сохранение настроек', 'Успешно', 'Правила автобронирования обновлены')
        _notify_in_app(state, 'Настройки FBO сохранены', 'Календарь целевых дат пересобран.', 'success')
        _save_state_sync(state)
        return state


async def add_excluded_date(value: str, reason: str = '') -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state['settings']
        dates = set(cfg.get('excluded_dates') or [])
        dates.add(value)
        cfg['excluded_dates'] = sorted(dates)
        _upsert_targets(state)
        _log(state, 'Исключение даты', 'Успешно', reason or 'Дата исключена вручную', target_date=value)
        _save_state_sync(state)
        return state


async def add_manual_target(value: str, comment: str = '') -> dict[str, Any]:
    async with _lock:
        _parse_date(value)
        state = _load_state_sync()
        cfg = state['settings']
        manual = set(cfg.get('manual_target_dates') or [])
        manual.add(value)
        cfg['manual_target_dates'] = sorted(manual)
        cfg['excluded_dates'] = [d for d in (cfg.get('excluded_dates') or []) if d != value]
        _upsert_targets(state)
        target = next((t for t in state.get('targets', []) if t.get('date') == value), None)
        if target:
            target['manual'] = True
            if comment:
                target['message'] = comment
        _refresh_postavleno_queue(state)
        _log(state, 'Добавление даты в календарь', 'Успешно', comment or 'Дата добавлена вручную', target_date=value)
        _notify_in_app(state, 'Дата добавлена в календарь FBO', f'{value}: {comment or "ручная дата"}', 'success')
        _save_state_sync(state)
        return state


async def edit_target_date(old_date: str, new_date: str, comment: str = '') -> dict[str, Any]:
    async with _lock:
        _parse_date(old_date)
        _parse_date(new_date)
        state = _load_state_sync()
        cfg = state['settings']
        _upsert_targets(state)
        target = next((t for t in state.get('targets', []) if t.get('date') == old_date), None)
        if not target:
            raise ValueError('Целевая дата не найдена')
        if old_date == new_date:
            if comment:
                target['message'] = comment
            _save_state_sync(state)
            return state

        manual = set(cfg.get('manual_target_dates') or [])
        manual.discard(old_date)
        manual.add(new_date)
        cfg['manual_target_dates'] = sorted(manual)

        # Если изменили дату, созданную автоматическим правилом, старая дата исключается,
        # чтобы она не появилась повторно при пересборке календаря.
        excluded = set(cfg.get('excluded_dates') or [])
        excluded.add(old_date)
        excluded.discard(new_date)
        cfg['excluded_dates'] = sorted(excluded)

        moved = dict(target)
        moved['date'] = new_date
        moved['manual'] = True
        moved['message'] = comment or f'Дата изменена вручную с {old_date} на {new_date}'
        state['targets'] = [t for t in state.get('targets', []) if t.get('date') not in {old_date, new_date}]
        state['targets'].append(moved)
        _upsert_targets(state)
        _log(state, 'Изменение даты в календаре', 'Успешно', f'{old_date} → {new_date}. {comment}', target_date=new_date)
        _notify_in_app(state, 'Дата в календаре FBO изменена', f'{old_date} → {new_date}', 'success')
        _save_state_sync(state)
        return state


async def run_check_once(manual: bool = False) -> dict[str, Any]:
    """Safe MVP check: creates target calendar, checks configuration, and marks next date as searching.

    Real booking adapter is intentionally isolated: if WB publishes/permits a stable booking endpoint,
    it can be added in this function without changing the UI. Playwright can also be attached here.
    """
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state['settings']
        if not cfg.get('enabled'):
            _log(state, 'Проверка окна', 'Отключено', 'Автобронирование выключено')
            _save_state_sync(state)
            return state
        if not settings.wb_api_token:
            _log(state, 'Проверка окна', 'Ошибка', 'WB_API_TOKEN не найден в .env')
            _notify_in_app(state, 'WB токен не найден', 'Добавь WB_API_TOKEN в .env и перезапусти backend.', 'error')
            state.setdefault('adapter_status', {})['wb_token_found'] = False
            _save_state_sync(state)
            return state
        state.setdefault('adapter_status', {})['wb_token_found'] = True
        open_targets = [t for t in state.get('targets', []) if t.get('status') not in {'booked', 'skipped_holiday'}]
        if not open_targets:
            _log(state, 'Проверка окна', 'Нет целей', 'Все даты на горизонте закрыты или исключены')
            _save_state_sync(state)
            return state
        target = open_targets[0]
        target['status'] = 'searching'
        target['status_label'] = _STATUS_LABELS['searching']
        target['last_checked_at'] = _now_iso()
        target['attempts'] = int(target.get('attempts') or 0) + 1
        target['message'] = 'Гибридный безопасный режим: дата активна в очереди POSTAVLENO, финальная бронь будет через ЛК WB/Playwright после подключения сессий.'
        _log(state, 'Поиск слота', 'Гибридный режим', target['message'], target_date=target.get('date'))
        if manual:
            _notify_in_app(state, 'Проверка FBO запущена', f"Целевая дата: {target.get('date')}. Модуль готов к подключению адаптера бронирования.", 'info')
        _save_state_sync(state)
        return state


async def mark_demo_booked(target_date: str) -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state['settings']
        target = next((t for t in state.get('targets', []) if t.get('date') == target_date), None)
        if not target:
            raise ValueError('Целевая дата не найдена')
        target.update({
            'status': 'booked',
            'status_label': _STATUS_LABELS['booked'],
            'warehouse': (cfg.get('warehouses') or ['Коледино'])[0],
            'slot_time': '13:00–14:00',
            'coefficient': min(int(cfg.get('max_coefficient') or 20), 20),
            'draft_id': f"WB-FBO-{target_date.replace('-', '')}",
            'last_checked_at': _now_iso(),
            'message': 'Демо-бронь отмечена вручную в интерфейсе. Это не отправляет действие в WB.',
        })
        _log(state, 'Демо-бронирование', 'Успешно', target['message'], target_date=target_date, warehouse=target['warehouse'], draft_id=target['draft_id'])
        _notify_in_app(state, 'Демо-окно отмечено как забронированное', f"{target_date}, {target['warehouse']}, {target['slot_time']}", 'success')
        _save_state_sync(state)
        return state



async def update_postavleno_account(payload: dict[str, Any]) -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        if 'postavleno_account_label' in payload:
            cfg['postavleno_account_label'] = payload.get('postavleno_account_label') or 'Не подключен'
        if 'postavleno_session_alias' in payload:
            cfg['postavleno_session_alias'] = payload.get('postavleno_session_alias') or 'default'
        if 'postavleno_enabled' in payload:
            cfg['postavleno_enabled'] = bool(payload.get('postavleno_enabled'))
        _upsert_targets(state)
        _log(state, 'POSTAVLENO аккаунт', 'Обновлено', f"Аккаунт: {cfg.get('postavleno_account_label')} / сессия: {cfg.get('postavleno_session_alias')}")
        _notify_in_app(state, 'POSTAVLENO аккаунт обновлен', 'Аккаунт можно сменить в настройках без правки кода.', 'success')
        _save_state_sync(state)
        return state


async def simulate_postavleno_found(target_date: str, warehouse: str = 'Коледино', slot_time: str = '13:00–14:00', coefficient: int | None = None) -> dict[str, Any]:
    """Dev-safe stand-in for a real POSTAVLENO message parser.

    Real adapter will replace this when Telegram user-session is connected. It lets
    the UI and queue logic be tested without touching WB or the external bot.
    """
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state['settings']
        target = next((t for t in state.get('targets', []) if t.get('date') == target_date), None)
        if not target:
            raise ValueError('Целевая дата не найдена')
        target.update({
            'status': 'found',
            'status_label': _STATUS_LABELS['found'],
            'warehouse': warehouse,
            'slot_time': slot_time,
            'coefficient': coefficient if coefficient is not None else min(int(cfg.get('max_coefficient') or 20), 20),
            'last_checked_at': _now_iso(),
            'postavleno_status': 'slot_found',
            'postavleno_status_label': 'Окно найдено ботом',
            'message': 'POSTAVLENO сообщил об окне. Следующий шаг — финальная бронь через ЛК WB/Playwright.',
        })
        _log(state, 'POSTAVLENO: окно найдено', 'Найден слот', target['message'], target_date=target_date, warehouse=warehouse)
        _notify_in_app(state, 'POSTAVLENO нашел окно', f"{target_date}, {warehouse}, {slot_time}. Финальная бронь: ЛК WB.", 'success')
        _refresh_postavleno_queue(state)
        _save_state_sync(state)
        return state




def _telegram_session_file(alias: str | None) -> Path:
    safe = ''.join(ch for ch in (alias or 'default') if ch.isalnum() or ch in {'_', '-', '.'}) or 'default'
    TELEGRAM_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return TELEGRAM_SESSION_DIR / safe


def _extract_dates_from_text(text: str) -> list[str]:
    import re
    found: list[str] = []
    for m in re.finditer(r'(?<!\d)(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})(?!\d)', text or ''):
        day, month, year = m.groups()
        year = ('20' + year) if len(year) == 2 else year
        try:
            found.append(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            pass
    for m in re.finditer(r'(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)', text or ''):
        try:
            found.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat())
        except ValueError:
            pass
    return sorted(set(found))


def _extract_warehouse_from_text(text: str, cfg: dict[str, Any]) -> str:
    low = (text or '').lower()
    for wh in cfg.get('warehouses') or []:
        if str(wh).lower() in low:
            return wh
    return (cfg.get('warehouses') or ['Коледино'])[0]


def _extract_slot_time_from_text(text: str) -> str:
    import re
    m = re.search(r'(\d{1,2}:\d{2})\s*[—\-–]\s*(\d{1,2}:\d{2})', text or '')
    if m:
        return f'{m.group(1)}–{m.group(2)}'
    m = re.search(r'(\d{1,2}:\d{2})', text or '')
    return m.group(1) if m else '—'


def _extract_coefficient_from_text(text: str, cfg: dict[str, Any]) -> int | None:
    import re
    low = (text or '').lower().replace('х', 'x')
    matches = re.findall(r'(\d{1,3})\s*x', low)
    if matches:
        return int(matches[0])
    if 'бесплат' in low:
        return 0
    return None


def _apply_postavleno_message_to_state(state: dict[str, Any], message_text: str) -> bool:
    cfg = state.get('settings') or {}
    dates = _extract_dates_from_text(message_text)
    if not dates:
        return False
    changed = False
    target_dates = {t.get('date'): t for t in state.get('targets', [])}
    for d in dates:
        target = target_dates.get(d)
        if not target or target.get('status') in {'booked', 'skipped_holiday'}:
            continue
        warehouse = _extract_warehouse_from_text(message_text, cfg)
        slot_time = _extract_slot_time_from_text(message_text)
        coefficient = _extract_coefficient_from_text(message_text, cfg)
        if coefficient is not None and coefficient > int(cfg.get('max_coefficient') or 20):
            target['message'] = f'POSTAVLENO нашел окно, но коэффициент {coefficient}х выше лимита.'
            _log(state, 'POSTAVLENO: окно отклонено', 'Коэффициент выше лимита', target['message'], target_date=d, warehouse=warehouse)
            changed = True
            continue
        target.update({
            'status': 'found',
            'status_label': _STATUS_LABELS['found'],
            'warehouse': warehouse,
            'slot_time': slot_time,
            'coefficient': coefficient,
            'last_checked_at': _now_iso(),
            'postavleno_status': 'slot_found',
            'postavleno_status_label': 'Окно найдено ботом',
            'message': 'POSTAVLENO сообщил об окне. Запущена подготовка к финальной брони через ЛК WB.',
        })
        _log(state, 'POSTAVLENO: окно найдено', 'Найден слот', message_text[:500], target_date=d, warehouse=warehouse)
        _notify_in_app(state, 'POSTAVLENO нашел окно', f'{d}, {warehouse}, {slot_time}. Проверь бронь в ЛК WB.', 'success')
        changed = True
    if changed:
        _refresh_postavleno_queue(state)
    return changed



def _playwright_profile_dir(alias: str | None = None) -> Path:
    base = TELEGRAM_WEB_PROFILE_DIR
    safe = ''.join(ch for ch in (alias or 'default') if ch.isalnum() or ch in {'_', '-', '.'}) or 'default'
    profile = base / safe
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def _screenshot_to_base64() -> str | None:
    try:
        if TELEGRAM_WEB_SCREENSHOT.exists():
            import base64
            return base64.b64encode(TELEGRAM_WEB_SCREENSHOT.read_bytes()).decode('ascii')
    except Exception:
        return None
    return None


async def telegram_web_status() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        state['telegram_web'] = {
            'status': cfg.get('telegram_web_status') or 'not_connected',
            'source_mode': cfg.get('postavleno_source_mode') or 'telegram_web',
            'profile_alias': cfg.get('postavleno_session_alias') or 'default',
            'last_screenshot_at': cfg.get('telegram_web_last_screenshot_at'),
            'last_checked_at': cfg.get('telegram_web_last_checked_at'),
            'last_error': cfg.get('telegram_web_last_error') or '',
            'screenshot_base64': _screenshot_to_base64(),
            'note': 'Telegram Web используется вместо my.telegram.org. Вход выполняется через QR/код в браузерной сессии Playwright, которая хранится локально.',
        }
        _save_state_sync(state)
        return state


async def start_telegram_web_login() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        alias = cfg.get('postavleno_session_alias') or 'default'
        cfg['postavleno_source_mode'] = 'telegram_web'
        cfg['telegram_web_status'] = 'opening'
        cfg['telegram_web_last_error'] = ''
        _save_state_sync(state)
    try:
        page = await _tg_web_get_page(alias, goto_home=True)
        await page.wait_for_timeout(3500)
        await _tg_web_save_screenshot(page)
        logged, _body_text = await _tg_web_is_logged_in(page)
        needs_2fa, _pwd_body = await _tg_web_needs_2fa_password(page)
        async with _lock:
            state = _load_state_sync()
            cfg = state['settings']
            if logged:
                cfg['telegram_web_status'] = 'connected'
            elif needs_2fa:
                cfg['telegram_web_status'] = '2fa_required'
            else:
                cfg['telegram_web_status'] = 'qr_ready'
            cfg['telegram_web_last_screenshot_at'] = _now_iso()
            cfg['postavleno_connection_status'] = cfg['telegram_web_status']
            cfg['telegram_web_last_error'] = 'Нужен облачный пароль Telegram 2FA' if needs_2fa and not logged else ''
            detail = 'Сессия уже авторизована' if logged else ('Telegram Web просит облачный пароль 2FA. Введи его в поле и нажми “Подтвердить 2FA”.' if needs_2fa else 'Живой QR открыт в сохраненной браузерной сессии. Сканируй его и затем нажми “Проверить вход”.')
            _log(state, 'Telegram Web', 'Открыт экран входа', detail)
            _notify_in_app(state, 'Telegram Web открыт', detail, 'success' if logged else 'info')
            state['telegram_web'] = {'screenshot_base64': _screenshot_to_base64()}
            _save_state_sync(state)
            return state
    except Exception as exc:
        async with _lock:
            state = _load_state_sync()
            cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
            cfg['telegram_web_status'] = 'error'
            cfg['telegram_web_last_error'] = str(exc)
            cfg['postavleno_connection_status'] = 'telegram_web_error'
            state['auth_error'] = str(exc)
            _log(state, 'Telegram Web', 'Ошибка открытия', str(exc))
            _save_state_sync(state)
            return state


async def check_telegram_web_login() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        alias = cfg.get('postavleno_session_alias') or 'default'
        cfg['postavleno_source_mode'] = 'telegram_web'
        _save_state_sync(state)
    try:
        page = await _tg_web_get_page(alias, goto_home=True)
        await page.wait_for_timeout(5500)
        await _tg_web_save_screenshot(page)
        logged, body_text = await _tg_web_is_logged_in(page)
        needs_2fa, _pwd_body = await _tg_web_needs_2fa_password(page)
        async with _lock:
            state = _load_state_sync()
            cfg = state['settings']
            if logged:
                status = 'connected'
            elif needs_2fa:
                status = '2fa_required'
            else:
                status = 'qr_waiting'
            cfg['telegram_web_status'] = status
            cfg['postavleno_connection_status'] = status
            cfg['telegram_web_last_screenshot_at'] = _now_iso()
            cfg['telegram_web_last_checked_at'] = _now_iso()
            cfg['telegram_web_last_error'] = '' if logged else ('Нужен облачный пароль Telegram 2FA' if needs_2fa else 'Telegram Web еще показывает экран входа/QR')
            _log(state, 'Telegram Web', 'Проверка входа', 'Сессия авторизована' if logged else ('Ожидает облачный пароль 2FA' if needs_2fa else 'Вход еще не выполнен: QR должен быть отсканирован из живого окна Telegram Web'))
            state['telegram_web'] = {'screenshot_base64': _screenshot_to_base64(), 'body_hint': body_text[:500]}
            _save_state_sync(state)
            return state
    except Exception as exc:
        async with _lock:
            state = _load_state_sync()
            cfg = state['settings']
            cfg['telegram_web_status'] = 'error'
            cfg['telegram_web_last_error'] = str(exc)
            cfg['postavleno_connection_status'] = 'telegram_web_error'
            state['auth_error'] = str(exc)
            _log(state, 'Telegram Web', 'Ошибка проверки входа', str(exc))
            _save_state_sync(state)
            return state

async def submit_telegram_web_2fa_password(password: str) -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        alias = cfg.get('postavleno_session_alias') or 'default'
        cfg['telegram_web_last_error'] = ''
        _save_state_sync(state)
    try:
        page = await _tg_web_get_page(alias, goto_home=False)
        await page.wait_for_timeout(1000)
        ok, detail = await _tg_web_fill_2fa_password(page, password)
        await _tg_web_save_screenshot(page)
        async with _lock:
            state = _load_state_sync()
            cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
            cfg['telegram_web_status'] = 'connected' if ok else '2fa_required'
            cfg['postavleno_connection_status'] = cfg['telegram_web_status']
            cfg['telegram_web_last_checked_at'] = _now_iso()
            cfg['telegram_web_last_screenshot_at'] = _now_iso()
            cfg['telegram_web_last_error'] = '' if ok else str(detail)[:500]
            _log(state, 'Telegram Web', 'Подтверждение 2FA', 'Сессия авторизована' if ok else str(detail)[:500])
            _notify_in_app(state, 'Telegram Web 2FA', 'Облачный пароль принят, Telegram Web авторизован' if ok else 'Telegram Web не принял пароль 2FA. Проверь пароль и повтори.', 'success' if ok else 'error')
            state['telegram_web'] = {'screenshot_base64': _screenshot_to_base64()}
            if ok:
                state.pop('auth_error', None)
            _save_state_sync(state)
            return state
    except Exception as exc:
        async with _lock:
            state = _load_state_sync()
            cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
            cfg['telegram_web_status'] = '2fa_required'
            cfg['telegram_web_last_error'] = str(exc)
            cfg['postavleno_connection_status'] = '2fa_required'
            state['auth_error'] = str(exc)
            _log(state, 'Telegram Web', 'Ошибка подтверждения 2FA', str(exc))
            _save_state_sync(state)
            return state


async def _tg_click_text(page, variants: list[str], timeout: int = 2500) -> str | None:
    """Try to click a Telegram Web button/text by several possible labels."""
    for label in variants:
        try:
            loc = page.get_by_text(label, exact=True).last
            if await loc.count():
                await loc.click(timeout=timeout)
                await page.wait_for_timeout(900)
                return label
        except Exception:
            pass
        try:
            loc = page.get_by_text(re.compile(re.escape(label), re.I)).last
            if await loc.count():
                await loc.click(timeout=timeout)
                await page.wait_for_timeout(900)
                return label
        except Exception:
            pass
    return None


async def _tg_type_message(page, text: str) -> bool:
    for sel in ['div[contenteditable="true"][role="textbox"]', 'div[contenteditable="true"]', '[contenteditable="true"]']:
        try:
            box = page.locator(sel).last
            if await box.count():
                await box.click(timeout=4000)
                await box.fill(text, timeout=4000)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
    return False


def _date_variants_for_bot(iso_date: str) -> dict[str, str]:
    d = _parse_date(iso_date)
    return {'iso': d.isoformat(), 'ru': d.strftime('%d.%m.%Y'), 'day': str(d.day), 'day2': f'{d.day:02d}', 'month': d.strftime('%m'), 'year': str(d.year)}


async def _tg_select_calendar_date(page, iso_date: str) -> list[str]:
    """Best-effort date selection: choose the same day as start and end."""
    v = _date_variants_for_bot(iso_date)
    clicks: list[str] = []
    for _ in range(8):
        label = await _tg_click_text(page, [v['day2'], v['day']], timeout=1800)
        if label:
            clicks.append(f'дата:{label}')
            await page.wait_for_timeout(900)
            break
        moved = await _tg_click_text(page, ['›', '>', 'Следующий', 'Вперед', '➡️'], timeout=1200)
        if moved:
            clicks.append(f'календарь:{moved}')
        else:
            break
    await _tg_click_text(page, ['Далее'], timeout=1200)
    second = await _tg_click_text(page, [v['day2'], v['day']], timeout=1800)
    if second:
        clicks.append(f'дата_конец:{second}')
    return clicks


async def _postavleno_fill_one_request(page, target: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, list[str]]:
    steps: list[str] = []
    target_date = target.get('date')
    date_ru = _date_variants_for_bot(target_date)['ru']
    warehouses = cfg.get('warehouses') or ['Коледино', 'Электросталь']
    max_coeff = str(cfg.get('max_coefficient') or 20)

    clicked = await _tg_click_text(page, ['Создать запрос', 'Новый запрос', 'Добавить запрос', 'Разместить запрос', 'Найти окно', 'Поиск окна', 'Создать поиск', 'Начать поиск', 'Запустить поиск', 'Начать'])
    if clicked:
        steps.append(f'старт:{clicked}')
    else:
        if await _tg_type_message(page, '/start'):
            steps.append('/start')
            await page.wait_for_timeout(1800)
        clicked = await _tg_click_text(page, ['Создать запрос', 'Новый запрос', 'Добавить запрос', 'Разместить запрос', 'Найти окно', 'Поиск окна', 'Создать поиск', 'Начать поиск', 'Запустить поиск', 'Начать'])
        if clicked:
            steps.append(f'старт:{clicked}')

    for wh in warehouses:
        clicked = await _tg_click_text(page, [wh])
        if clicked:
            steps.append(f'склад:{clicked}')
    clicked = await _tg_click_text(page, ['Суперсейф', 'суперсейф', 'Сейф', 'Ювелирные изделия'])
    if clicked:
        steps.append(f'тип:{clicked}')
    clicked = await _tg_click_text(page, [f'{max_coeff}х', f'{max_coeff}x', max_coeff, f'до {max_coeff}', f'до {max_coeff}х'])
    if clicked:
        steps.append(f'коэф:{clicked}')

    for _ in range(4):
        clicked = await _tg_click_text(page, ['Далее'])
        if clicked:
            steps.append('далее')
        else:
            break

    steps.extend(await _tg_select_calendar_date(page, target_date))
    for _ in range(2):
        clicked = await _tg_click_text(page, ['Далее'])
        if clicked:
            steps.append('далее')
    finish = await _tg_click_text(page, ['Готово', 'Создать', 'Сохранить', 'Подтвердить', 'Разместить', 'Запустить', 'Начать поиск'])
    if finish:
        steps.append(f'финиш:{finish}')

    body = ''
    try:
        body = await page.locator('body').inner_text(timeout=5000)
    except Exception:
        pass
    success_markers = ['создан', 'добавлен', 'запрос', 'поиск', 'активн', 'готово', 'принят']
    success = (date_ru in body or target_date in body) and any(m in body.lower() for m in success_markers)
    success = success or (any(x.startswith('дата') for x in steps) and any(x.startswith('финиш') for x in steps))
    return success, steps


async def create_postavleno_requests_web() -> dict[str, Any]:
    await sync_postavleno_requests()
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        alias = cfg.get('postavleno_session_alias') or 'default'
        bot_username = (cfg.get('postavleno_bot_username') or '@POSTAVLENOru_BOT').lstrip('@')
        targets = [t for t in state.get('targets', []) if t.get('postavleno_status') == 'active_request' and not t.get('postavleno_submitted_at')]
        targets = targets[: int(cfg.get('postavleno_max_active_requests') or 5)]
        if not targets:
            _log(state, 'POSTAVLENO Web: размещение запросов', 'Нечего размещать', 'Нет активных дат без отметки о размещении')
            _save_state_sync(state)
            return state
        _save_state_sync(state)

    try:
        page = await _tg_web_get_page(alias, goto_home=True)
        await page.wait_for_timeout(3500)
        logged, body = await _tg_web_is_logged_in(page)
        if not logged:
            await _tg_web_save_screenshot(page)
            raise RuntimeError('Telegram Web не авторизован: открыт экран входа/QR. Нажми “Открыть Telegram Web / показать QR”, отсканируй живой QR и затем “Проверить вход”.')
        body = await _tg_web_open_bot(page, bot_username)
        logged, _ = await _tg_web_is_logged_in(page)
        if not logged:
            await _tg_web_save_screenshot(page)
            raise RuntimeError('Telegram Web не авторизован после открытия POSTAVLENO. Повтори вход через QR.')

        results: list[str] = []
        for target in targets:
            ok, steps = await _postavleno_fill_one_request(page, target, cfg)
            results.append(f"{target.get('date')}: {'создан' if ok else 'не подтвержден'} ({', '.join(steps) or 'нет кликов'})")
            async with _lock:
                state = _load_state_sync()
                real = next((t for t in state.get('targets', []) if t.get('date') == target.get('date')), None)
                if real:
                    real['postavleno_request_steps'] = steps
                    real['postavleno_last_attempt_at'] = _now_iso()
                    if ok:
                        real['postavleno_submitted_at'] = _now_iso()
                        real['postavleno_status'] = 'active_request'
                        real['postavleno_status_label'] = 'Размещен в POSTAVLENO'
                        real['message'] = 'Запрос физически размещен в POSTAVLENO через Telegram Web'
                    else:
                        real['message'] = 'Не удалось надежно подтвердить размещение запроса в POSTAVLENO; см. скриншот и журнал'
                _save_state_sync(state)
            await page.wait_for_timeout(1500)
        POSTAVLENO_REQUEST_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(POSTAVLENO_REQUEST_SCREENSHOT), full_page=True)
        await _tg_web_save_screenshot(page)

        async with _lock:
            state = _load_state_sync()
            cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
            cfg['telegram_web_status'] = 'connected'
            cfg['postavleno_connection_status'] = 'connected'
            cfg['postavleno_last_request_submit_at'] = _now_iso()
            cfg['telegram_web_last_screenshot_at'] = _now_iso()
            cfg['telegram_web_last_error'] = ''
            submitted = sum(1 for line in results if ': создан' in line)
            result_status = 'Выполнено' if submitted else 'Нужна подстройка'
            _log(state, 'POSTAVLENO Web: размещение запросов', result_status, '; '.join(results))
            _notify_in_app(state, 'POSTAVLENO: размещение запросов', f'Создано/подтверждено: {submitted} из {len(results)}. Если 0 — пришли скриншот, подстроим кнопки бота.', 'success' if submitted else 'error')
            state['telegram_web'] = {'screenshot_base64': _screenshot_to_base64()}
            state.pop('auth_error', None)
            _save_state_sync(state)
            return state
    except Exception as exc:
        async with _lock:
            state = _load_state_sync()
            cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
            cfg['telegram_web_status'] = 'qr_waiting' if 'не авторизован' in str(exc) else 'error'
            cfg['telegram_web_last_error'] = str(exc)
            cfg['postavleno_connection_status'] = cfg['telegram_web_status']
            state['auth_error'] = str(exc)
            _log(state, 'POSTAVLENO Web: размещение запросов', 'Ошибка', str(exc))
            _notify_in_app(state, 'Ошибка размещения запросов POSTAVLENO', str(exc), 'error')
            _save_state_sync(state)
            return state


async def check_postavleno_web_messages() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        alias = cfg.get('postavleno_session_alias') or 'default'
        bot_username = (cfg.get('postavleno_bot_username') or '@POSTAVLENOru_BOT').lstrip('@')
        cfg['postavleno_source_mode'] = 'telegram_web'
        _save_state_sync(state)
    try:
        page = await _tg_web_get_page(alias, goto_home=True)
        await page.wait_for_timeout(3000)
        logged, body_text = await _tg_web_is_logged_in(page)
        if not logged:
            await _tg_web_save_screenshot(page)
            raise RuntimeError('Telegram Web не авторизован: открыт экран входа/QR. Отсканируй QR и нажми “Проверить вход”.')
        body_text = await _tg_web_open_bot(page, bot_username)
        await page.wait_for_timeout(3000)
        await _tg_web_save_screenshot(page)
        try:
            body_text = await page.locator('body').inner_text(timeout=20000)
        except Exception:
            pass
        lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
        relevant = []
        for ln in lines[-160:]:
            low = ln.lower()
            if any(x in low for x in ['окн', 'слот', 'поставка', 'коледино', 'электросталь', 'коэффициент', 'найден', 'доступн']):
                relevant.append(ln)
        text_to_parse = '\n'.join(relevant[-50:] or lines[-50:])
        async with _lock:
            state = _load_state_sync()
            _upsert_targets(state)
            cfg = state['settings']
            cfg['telegram_web_last_checked_at'] = _now_iso()
            cfg['telegram_web_last_screenshot_at'] = _now_iso()
            cfg['postavleno_last_checked_at'] = _now_iso()
            cfg['postavleno_last_message'] = text_to_parse[:1000]
            changed = _apply_postavleno_message_to_state(state, text_to_parse)
            cfg['telegram_web_status'] = 'connected'
            cfg['postavleno_connection_status'] = 'connected'
            cfg['telegram_web_last_error'] = ''
            state.pop('auth_error', None)
            _log(state, 'POSTAVLENO Web: проверка сообщений', 'Выполнено', 'Найдено окно по календарю' if changed else 'Совпадений по датам календаря не найдено')
            state['telegram_web'] = {'screenshot_base64': _screenshot_to_base64()}
            _save_state_sync(state)
            return state
    except Exception as exc:
        async with _lock:
            state = _load_state_sync()
            cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
            cfg['telegram_web_status'] = 'qr_waiting' if 'не авторизован' in str(exc) else 'error'
            cfg['telegram_web_last_error'] = str(exc)
            cfg['postavleno_connection_status'] = cfg['telegram_web_status']
            state['auth_error'] = str(exc)
            _log(state, 'POSTAVLENO Web: проверка сообщений', 'Ошибка', str(exc))
            _save_state_sync(state)
            return state
async def request_postavleno_login_code(payload: dict[str, Any]) -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        cfg['postavleno_phone'] = payload.get('postavleno_phone') or cfg.get('postavleno_phone') or ''
        cfg['postavleno_telegram_api_id'] = str(payload.get('postavleno_telegram_api_id') or cfg.get('postavleno_telegram_api_id') or '')
        cfg['postavleno_telegram_api_hash'] = payload.get('postavleno_telegram_api_hash') or cfg.get('postavleno_telegram_api_hash') or ''
        cfg['postavleno_session_alias'] = payload.get('postavleno_session_alias') or cfg.get('postavleno_session_alias') or 'default'
        if not cfg['postavleno_phone'] or not cfg['postavleno_telegram_api_id'] or not cfg['postavleno_telegram_api_hash']:
            cfg['postavleno_connection_status'] = 'missing_credentials'
            state['auth_error'] = 'Заполни телефон, API ID и API Hash.'
            _save_state_sync(state)
            return state
    try:
        from telethon import TelegramClient
        session_file = _telegram_session_file(cfg['postavleno_session_alias'])
        client = TelegramClient(str(session_file), int(cfg['postavleno_telegram_api_id']), cfg['postavleno_telegram_api_hash'])
        await client.connect()
        sent = await client.send_code_request(cfg['postavleno_phone'])
        await client.disconnect()
        async with _lock:
            state = _load_state_sync()
            cfg = state['settings']
            cfg['postavleno_phone_code_hash'] = getattr(sent, 'phone_code_hash', '')
            cfg['postavleno_connection_status'] = 'code_sent'
            state.pop('auth_error', None)
            _log(state, 'Telegram user-session', 'Код отправлен', f"Аккаунт: {cfg.get('postavleno_phone')}, alias: {cfg.get('postavleno_session_alias')}")
            _notify_in_app(state, 'Код Telegram отправлен', 'Введи код из Telegram для подключения POSTAVLENO-сессии.', 'info')
            _save_state_sync(state)
            return state
    except Exception as exc:
        async with _lock:
            state = _load_state_sync()
            state['settings']['postavleno_connection_status'] = 'error'
            state['auth_error'] = str(exc)
            _log(state, 'Telegram user-session', 'Ошибка', str(exc))
            _save_state_sync(state)
            return state


async def confirm_postavleno_login_code(code: str, password: str | None = None) -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.get('settings') or {}
        phone = cfg.get('postavleno_phone')
        api_id = cfg.get('postavleno_telegram_api_id')
        api_hash = cfg.get('postavleno_telegram_api_hash')
        alias = cfg.get('postavleno_session_alias') or 'default'
        phone_code_hash = cfg.get('postavleno_phone_code_hash')
    try:
        from telethon import TelegramClient
        from telethon.errors import SessionPasswordNeededError
        session_file = _telegram_session_file(alias)
        client = TelegramClient(str(session_file), int(api_id), api_hash)
        await client.connect()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                await client.disconnect()
                async with _lock:
                    state = _load_state_sync()
                    state['settings']['postavleno_connection_status'] = 'password_required'
                    _save_state_sync(state)
                    return state
            await client.sign_in(password=password)
        me = await client.get_me()
        await client.disconnect()
        async with _lock:
            state = _load_state_sync()
            cfg = state['settings']
            cfg['postavleno_connection_status'] = 'connected'
            cfg['postavleno_account_label'] = getattr(me, 'username', None) or getattr(me, 'first_name', None) or phone or alias
            state.pop('auth_error', None)
            _log(state, 'Telegram user-session', 'Подключено', f"{cfg.get('postavleno_account_label')} / {alias}")
            _notify_in_app(state, 'Telegram-сессия POSTAVLENO подключена', 'Теперь можно проверять сообщения от @POSTAVLENOru_BOT.', 'success')
            _save_state_sync(state)
            return state
    except Exception as exc:
        async with _lock:
            state = _load_state_sync()
            state['settings']['postavleno_connection_status'] = 'error'
            state['auth_error'] = str(exc)
            _log(state, 'Telegram user-session', 'Ошибка подтверждения', str(exc))
            _save_state_sync(state)
            return state


async def check_postavleno_bot_messages() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state.get('settings') or {}
        api_id = cfg.get('postavleno_telegram_api_id')
        api_hash = cfg.get('postavleno_telegram_api_hash')
        alias = cfg.get('postavleno_session_alias') or 'default'
        bot_username = (cfg.get('postavleno_bot_username') or '@POSTAVLENOru_BOT').lstrip('@')
        if not api_id or not api_hash:
            state['auth_error'] = 'Telegram user-session не настроена: нет API ID/API Hash.'
            _save_state_sync(state)
            return state
    try:
        from telethon import TelegramClient
        session_file = _telegram_session_file(alias)
        client = TelegramClient(str(session_file), int(api_id), api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            async with _lock:
                state = _load_state_sync()
                state['settings']['postavleno_connection_status'] = 'not_authorized'
                state['auth_error'] = 'Сессия Telegram не авторизована. Отправь код и подтверди вход.'
                _save_state_sync(state)
                return state
        entity = await client.get_entity(bot_username)
        messages = await client.get_messages(entity, limit=20)
        await client.disconnect()
        async with _lock:
            state = _load_state_sync()
            _upsert_targets(state)
            cfg = state['settings']
            cfg['postavleno_last_checked_at'] = _now_iso()
            changed = False
            for msg in reversed(messages):
                text = getattr(msg, 'message', '') or ''
                if text:
                    cfg['postavleno_last_message'] = text[:1000]
                    if _apply_postavleno_message_to_state(state, text):
                        changed = True
            cfg['postavleno_connection_status'] = 'connected'
            state.pop('auth_error', None)
            _log(state, 'POSTAVLENO: проверка сообщений', 'Выполнено', 'Найдено изменение по целевой дате' if changed else 'Совпадений по датам календаря не найдено')
            _save_state_sync(state)
            return state
    except Exception as exc:
        async with _lock:
            state = _load_state_sync()
            state['auth_error'] = str(exc)
            state['settings']['postavleno_connection_status'] = 'error'
            _log(state, 'POSTAVLENO: проверка сообщений', 'Ошибка', str(exc))
            _save_state_sync(state)
            return state


async def sync_postavleno_requests() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state['settings']
        active = [t for t in state.get('targets', []) if t.get('postavleno_status') == 'active_request'][: int(cfg.get('postavleno_max_active_requests') or 5)]
        cfg['postavleno_last_request_sync_at'] = _now_iso()
        for t in active:
            t['postavleno_request_payload'] = (cfg.get('postavleno_request_template') or '').format(
                warehouse='/'.join(cfg.get('warehouses') or []),
                supply_type=cfg.get('supply_type'),
                date=t.get('date'),
                max_coefficient=cfg.get('max_coefficient'),
            )
        _log(state, 'POSTAVLENO: очередь запросов', 'Подготовлено', f'Активных запросов: {len(active)}')
        _notify_in_app(state, 'Очередь POSTAVLENO подготовлена', f'Активных дат: {len(active)}. Удаление старых запросов не используется.', 'info')
        _save_state_sync(state)
        return state


async def wb_cabinet_status() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        state['wb_cabinet_status'] = {
            'status': cfg.get('wb_cabinet_connection_status') or 'not_connected',
            'profile_alias': cfg.get('wb_cabinet_profile_alias') or 'default',
            'last_action': cfg.get('wb_cabinet_last_action') or '',
            'note': 'Playwright-адаптер подготовлен как финальный контур бронирования через ЛК WB. Реальный вход выполняется локально в браузере, пароль не хранится в коде.',
        }
        _save_state_sync(state)
        return state


async def mark_wb_cabinet_connected(profile_alias: str = 'default') -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        cfg['wb_cabinet_connection_status'] = 'ready_for_local_auth'
        cfg['wb_cabinet_profile_alias'] = profile_alias or 'default'
        cfg['wb_cabinet_last_action'] = 'Пользователь включил контур ЛК WB. Следующий шаг — локальная авторизация Playwright.'
        _log(state, 'ЛК WB / Playwright', 'Подготовлено', cfg['wb_cabinet_last_action'])
        _notify_in_app(state, 'Контур ЛК WB подготовлен', 'Для боевого бронирования понадобится один раз авторизоваться в ЛК WB локально.', 'info')
        _save_state_sync(state)
        return state


async def full_hybrid_tick() -> dict[str, Any]:
    state = await sync_postavleno_requests()
    try:
        current_for_submit = _load_state_sync()
        if (current_for_submit.get('settings') or {}).get('postavleno_source_mode', 'telegram_web') == 'telegram_web':
            state = await create_postavleno_requests_web()
    except Exception:
        pass
    try:
        current = _load_state_sync()
        mode = (current.get('settings') or {}).get('postavleno_source_mode') or 'telegram_web'
        if mode == 'telegram_api':
            state = await check_postavleno_bot_messages()
        else:
            state = await check_postavleno_web_messages()
    except Exception:
        pass
    async with _lock:
        state = _load_state_sync()
        found = [t for t in state.get('targets', []) if t.get('status') == 'found']
        if found:
            t = found[0]
            _log(state, 'Гибридный сценарий', 'Ожидает финальной брони', f"Дата {t.get('date')} найдена POSTAVLENO; нужна финальная бронь через ЛК WB/Playwright", target_date=t.get('date'), warehouse=t.get('warehouse'))
            _notify_in_app(state, 'Найдено окно, нужна финальная бронь', f"{t.get('date')} · {t.get('warehouse')} · {t.get('slot_time')}", 'success')
        else:
            _log(state, 'Гибридный сценарий', 'Окон не найдено', 'Очередь POSTAVLENO обновлена, совпадений в сообщениях нет')
        _save_state_sync(state)
        return state

async def send_telegram_test() -> dict[str, Any]:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return {'ok': False, 'message': 'TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заполнены в .env'}
    text = 'KARATOV CX Hub: тест уведомлений автобронирования FBO ✅'
    url = f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage'
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json={'chat_id': settings.telegram_chat_id, 'text': text})
    return {'ok': r.status_code < 300, 'status_code': r.status_code, 'message': r.text[:500]}


def send_email_test_sync() -> dict[str, Any]:
    if not settings.smtp_host or not settings.email_to:
        return {'ok': False, 'message': 'SMTP_HOST или EMAIL_TO не заполнены в .env'}
    msg = EmailMessage()
    msg['Subject'] = 'KARATOV CX Hub: тест FBO уведомлений'
    msg['From'] = settings.email_from or settings.smtp_user or 'fbo-booking@localhost'
    msg['To'] = settings.email_to
    msg.set_content('Тест email-уведомлений модуля автобронирования FBO.')
    port = int(settings.smtp_port or 587)
    with smtplib.SMTP(settings.smtp_host, port, timeout=20) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    return {'ok': True, 'message': 'Email отправлен'}



async def _send_telegram_message(text: str) -> dict[str, Any]:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return {'ok': False, 'message': 'TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заполнены'}
    url = f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage'
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, json={'chat_id': settings.telegram_chat_id, 'text': text})
    return {'ok': r.status_code < 300, 'status_code': r.status_code, 'message': r.text[:500]}


def _send_email_message_sync(subject: str, body: str) -> dict[str, Any]:
    if not settings.smtp_host or not settings.email_to:
        return {'ok': False, 'message': 'SMTP_HOST или EMAIL_TO не заполнены'}
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = settings.email_from or settings.smtp_user or 'fbo-booking@localhost'
    msg['To'] = settings.email_to
    msg.set_content(body)
    port = int(settings.smtp_port or 587)
    with smtplib.SMTP(settings.smtp_host, port, timeout=20) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)
    return {'ok': True, 'message': 'Email отправлен'}


async def _notify_external_if_enabled(state: dict[str, Any], title: str, body: str, level: str = 'info') -> None:
    cfg = state.get('settings') or {}
    text = f"{title}\n{body}"
    if cfg.get('telegram_enabled'):
        try:
            result = await _send_telegram_message(text)
            _log(state, 'Telegram уведомление FBO', 'Отправлено' if result.get('ok') else 'Ошибка', result.get('message', '')[:300])
        except Exception as exc:
            _log(state, 'Telegram уведомление FBO', 'Ошибка', str(exc))
    if cfg.get('email_enabled'):
        try:
            result = _send_email_message_sync(title, body)
            _log(state, 'Email уведомление FBO', 'Отправлено' if result.get('ok') else 'Ошибка', result.get('message', '')[:300])
        except Exception as exc:
            _log(state, 'Email уведомление FBO', 'Ошибка', str(exc))


def _parse_iso_z(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return None


async def _handle_autopilot_post_monitor_notifications(state: dict[str, Any], found_before: set[str]) -> None:
    cfg = state.get('settings') or {}
    found_targets = [t for t in state.get('targets', []) if t.get('status') == 'found']
    for t in found_targets:
        d = t.get('date')
        if not d or d in found_before or t.get('external_found_notified_at'):
            continue
        t['external_found_notified_at'] = _now_iso()
        title = '✅ WB FBO: найдено окно'
        body = f"Дата: {d}\nСклад: {t.get('warehouse') or '—'}\nВремя: {t.get('slot_time') or '—'}\nКоэффициент: {t.get('coefficient') if t.get('coefficient') is not None else '—'}\nСледующий шаг: хаб открывает ЛК WB для создания/привязки черновика."
        _notify_in_app(state, title, body, 'success')
        await _notify_external_if_enabled(state, title, body, 'success')

    if found_targets:
        return
    now_dt = datetime.utcnow()
    last = _parse_iso_z(cfg.get('scheduler_last_not_found_notified_at'))
    minutes = int(cfg.get('not_found_notify_after_minutes') or 120)
    if last is None or (now_dt - last).total_seconds() >= minutes * 60:
        cfg['scheduler_last_not_found_notified_at'] = _now_iso()
        title = 'WB FBO: окна пока не найдены'
        body = f"Автопоиск продолжает работу по расписанию {cfg.get('search_from', '09:00')}–{cfg.get('search_to', '21:00')}. Следующая попытка будет выполнена автоматически."
        _notify_in_app(state, title, body, 'info')
        await _notify_external_if_enabled(state, title, body, 'info')


async def wb_first_autopilot_tick(manual: bool = False) -> dict[str, Any]:
    """Autonomous WB-cabinet-first cycle.

    Main source is the seller cabinet opened by Playwright. WB API is not used in
    the normal loop, so 429 cannot stop the FBO autopilot. API remains only a
    separate diagnostic button.
    """
    if _runtime.get('cycle_running'):
        state = _load_state_sync()
        _log(state, 'ЛК WB автопилот', 'Пропущено', 'Предыдущий цикл еще выполняется')
        _save_state_sync(state)
        return state
    _runtime['cycle_running'] = True
    try:
        state0 = _load_state_sync()
        _upsert_targets(state0)
        cfg0 = state0.setdefault('settings', DEFAULT_SETTINGS.copy())
        found_before = {t.get('date') for t in state0.get('targets', []) if t.get('status') == 'found'}
        cfg0['scheduler_last_started_at'] = _now_iso()
        cfg0['scheduler_mode'] = 'wb_cabinet_autopilot'
        _save_state_sync(state0)

        state = await wb_cabinet_monitor_targets((cfg0.get('wb_cabinet_profile_alias') or 'default'), manual=manual)
        await _handle_autopilot_post_monitor_notifications(state, found_before)
        _save_state_sync(state)

        current = _load_state_sync()
        if any(t.get('status') == 'found' for t in current.get('targets', [])) and (current.get('settings') or {}).get('auto_book', True):
            current = await wb_cabinet_book_next_safe((current.get('settings') or {}).get('wb_cabinet_profile_alias') or 'default')
            state = current

        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        cfg['scheduler_last_finished_at'] = _now_iso()
        cfg['scheduler_next_retry_at'] = None
        _runtime['next_retry_at'] = None
        _runtime['last_message'] = 'ЛК WB автопилот выполнил цикл: открыл кабинет → проверил целевые даты → подготовил бронь при найденном окне'
        _log(state, 'ЛК WB автопилот', 'Цикл выполнен', _runtime['last_message'])
        _save_state_sync(state)
        return state
    finally:
        _runtime['cycle_running'] = False
async def fbo_booking_loop() -> None:
    await asyncio.sleep(12)
    while True:
        sleep_for = 120
        try:
            state = _load_state_sync()
            cfg = state.get('settings') or DEFAULT_SETTINGS
            enabled = bool(cfg.get('enabled')) and bool(cfg.get('scheduler_enabled', True))
            _runtime['running'] = enabled
            _runtime['last_tick_at'] = _now_iso()
            if enabled:
                now_t = datetime.now().time()
                start_t = _parse_time(cfg.get('search_from') or '09:00')
                end_t = _parse_time(cfg.get('search_to') or '21:00')
                next_retry = _parse_iso_z(cfg.get('scheduler_next_retry_at'))
                if next_retry and datetime.utcnow() < next_retry:
                    sleep_for = max(30, min(300, int((next_retry - datetime.utcnow()).total_seconds())))
                    _runtime['next_retry_at'] = cfg.get('scheduler_next_retry_at')
                    _runtime['last_message'] = f'Жду автоповтора после лимита WB API: {cfg.get("scheduler_next_retry_at")}'
                elif start_t <= now_t <= end_t:
                    await wb_first_autopilot_tick(manual=False)
                    state = _load_state_sync()
                    cfg = state.get('settings') or cfg
                    sleep_for = max(60, int(cfg.get('check_interval_seconds') or 300))
                else:
                    _runtime['last_message'] = f'Вне окна поиска {cfg.get("search_from", "09:00")}–{cfg.get("search_to", "21:00")}; автопоиск продолжится сам.'
                    sleep_for = 300
            else:
                _runtime['last_message'] = 'Автопоиск FBO выключен в настройках.'
                sleep_for = 300
        except Exception as exc:
            _runtime['last_error'] = str(exc)
            _runtime['last_message'] = 'Ошибка цикла; автоперезапуск через 2 минуты.'
            sleep_for = 120
        await asyncio.sleep(sleep_for)


def runtime_status() -> dict[str, Any]:
    return dict(_runtime)

# ============================
# WB-FIRST FBO BUILD OVERRIDES
# ============================
WB_CABINET_PROFILE_BASE = Path(os.getenv('WB_CABINET_PROFILE_DIR', '/app/playwright_profiles/wb_cabinet'))
WB_CABINET_SCREENSHOT = Path(os.getenv('WB_CABINET_SCREENSHOT', '/app/playwright_profiles/wb_cabinet_last.png'))
_WB_PLAYWRIGHT = None
_WB_CONTEXT = None
_WB_PAGE = None
_WB_ALIAS = None

_WB_ROUTE_PROGRESS: dict[str, Any] = {
    'running': False,
    'stop_requested': False,
    'step': '',
    'step_index': 0,
    'total_steps': 15,
    'mode': 'test',
    'last_updated_at': None,
    'screenshot_base64': '',
    'error': '',
}


def _wb_headers() -> dict[str, str]:
    token = settings.wb_api_token or os.getenv('WB_API_TOKEN', '')
    return {'Authorization': token, 'Content-Type': 'application/json'} if token else {}


async def _wb_http_probe(method: str, url: str, *, params: dict[str, Any] | None = None, json_body: Any = None, timeout: float = 20.0, cache_ttl_seconds: int = 0, min_delay_seconds: float = 3.0) -> dict[str, Any]:
    """WB API call with gentle throttling, cache and 429 backoff.

    WB returns 429 quickly if we burst several endpoints from the same token.
    This wrapper serializes calls and waits between them.
    """
    global _WB_API_LAST_CALL_AT, _WB_API_429_UNTIL
    headers = _wb_headers()
    if not headers:
        return {'ok': False, 'status_code': None, 'message': 'WB_API_TOKEN не найден в .env', 'sample': None}

    now_ts = asyncio.get_event_loop().time()
    key = json.dumps({'method': method.upper(), 'url': url, 'params': params or {}, 'json': json_body}, sort_keys=True, ensure_ascii=False)
    cached = _WB_API_CACHE.get(key)
    if cache_ttl_seconds and cached and now_ts - float(cached.get('created_at') or 0) < cache_ttl_seconds:
        out = dict(cached.get('response') or {})
        out['cached'] = True
        return out

    async with _WB_API_CALL_LOCK:
        now_ts = asyncio.get_event_loop().time()
        if _WB_API_429_UNTIL and now_ts < _WB_API_429_UNTIL:
            wait_left = int(_WB_API_429_UNTIL - now_ts)
            return {
                'ok': False,
                'status_code': 429,
                'rate_limited': True,
                'retry_after_seconds': wait_left,
                'message': f'WB API временно ограничил частоту запросов. Повтор через ~{wait_left} сек.',
                'sample': None,
            }
        elapsed = now_ts - _WB_API_LAST_CALL_AT
        if elapsed < min_delay_seconds:
            await asyncio.sleep(min_delay_seconds - elapsed)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method.upper() == 'GET':
                    resp = await client.get(url, headers=headers, params=params)
                elif method.upper() == 'POST':
                    resp = await client.post(url, headers=headers, params=params, json=json_body)
                else:
                    resp = await client.request(method.upper(), url, headers=headers, params=params, json=json_body)
            _WB_API_LAST_CALL_AT = asyncio.get_event_loop().time()
            text = resp.text[:1000]
            sample = None
            try:
                sample = resp.json()
                if isinstance(sample, list):
                    sample = sample[:200]
                elif isinstance(sample, dict):
                    trimmed = {}
                    for k in list(sample)[:20]:
                        v = sample[k]
                        if isinstance(v, list):
                            trimmed[k] = v[:200]
                        elif isinstance(v, dict):
                            trimmed[k] = {kk: v[kk] for kk in list(v)[:20]}
                        else:
                            trimmed[k] = v
                    sample = trimmed
            except Exception:
                sample = text

            if resp.status_code == 429:
                retry_after = resp.headers.get('Retry-After')
                try:
                    retry_seconds = max(60, int(float(retry_after))) if retry_after else 120
                except Exception:
                    retry_seconds = 120
                _WB_API_429_UNTIL = asyncio.get_event_loop().time() + retry_seconds
                return {
                    'ok': False,
                    'status_code': 429,
                    'rate_limited': True,
                    'retry_after_seconds': retry_seconds,
                    'message': f'WB API вернул 429 Too Many Requests. Автоповтор не раньше чем через ~{retry_seconds} сек. Подробности WB: {text[:350]}',
                    'sample': sample,
                }

            result = {
                'ok': 200 <= resp.status_code < 300,
                'status_code': resp.status_code,
                'message': 'OK' if 200 <= resp.status_code < 300 else text[:500],
                'sample': sample,
                'cached': False,
            }
            if cache_ttl_seconds and 200 <= resp.status_code < 300:
                _WB_API_CACHE[key] = {'created_at': asyncio.get_event_loop().time(), 'response': result}
            return result
        except Exception as exc:
            return {'ok': False, 'status_code': None, 'message': str(exc), 'sample': None}


async def wb_api_audit() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        checks: list[dict[str, Any]] = []
        token_found = bool(settings.wb_api_token or os.getenv('WB_API_TOKEN'))
        checks.append({
            'name': 'WB API token',
            'scope': 'Поставки / Supplies',
            'status': 'ok' if token_found else 'error',
            'details': 'Токен найден в окружении' if token_found else 'WB_API_TOKEN не найден в .env',
        })
        probes = [
            ('Коэффициенты приемки', 'GET', 'https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients', None, None, 'Официальный тарифный метод. Используется для первичного мониторинга доступности приемки. Кэшируется на 15 минут.', 900),
            ('Склады FBW/FBO', 'GET', 'https://supplies-api.wildberries.ru/api/v1/warehouses', None, None, 'Если метод доступен токену, подтягивает список складов. Кэшируется на сутки.', 86400),
            ('Список поставок FBW/FBO', 'POST', 'https://supplies-api.wildberries.ru/api/v1/supplies', None, {'limit': 10, 'offset': 0}, 'Если метод доступен токену, помогает найти существующие поставки/черновики. Кэшируется на 5 минут.', 300),
        ]
        for name, method, url, params, body, description, cache_ttl in probes:
            res = await _wb_http_probe(method, url, params=params, json_body=body, cache_ttl_seconds=cache_ttl, min_delay_seconds=3.0)
            status = 'ok' if res.get('ok') else ('warning' if res.get('status_code') in {400, 422, 429} else 'error')
            details = res.get('message') or ''
            if res.get('cached'):
                details = 'OK, использован кэш. ' + details
            checks.append({
                'name': name,
                'scope': url,
                'status': status,
                'details': details,
                'status_code': res.get('status_code'),
                'description': description,
                'sample': res.get('sample'),
            })
        checks.append({
            'name': 'Варианты приемки',
            'scope': 'POST https://supplies-api.wildberries.ru/api/v1/acceptance/options',
            'status': 'warning',
            'status_code': '—',
            'details': 'Endpoint не вызывается в audit без состава поставки: WB требует реальные barcodes/items, иначе возвращает 400. Проверять будем при наличии шаблонных баркодов/состава поставки.',
            'description': 'Метод нужен для проверки варианта приемки по конкретному составу поставки, а не для общего мониторинга календаря.',
        })
        checks.append({
            'name': 'Создание FBO/FBW черновика и бронирование окна',
            'scope': 'Публичный API WB',
            'status': 'warning',
            'details': 'В публичной документации нет подтвержденного стабильного метода полного цикла: создать FBO/FBW-черновик → выбрать окно → забронировать слот. Поэтому финальный контур оставлен через ЛК WB / Playwright safe-mode.',
            'description': 'Этот блок нужен, чтобы не путать FBS supplies API с FBO/FBW поставками.',
        })
        cfg['search_source_mode'] = 'wb_first'
        cfg['postavleno_enabled'] = False
        cfg['postavleno_connection_status'] = 'disabled_wb_first'
        cfg['wb_api_last_audit_at'] = _now_iso()
        cfg['wb_api_status'] = 'checked'
        cfg['wb_api_capabilities'] = checks
        _log(state, 'WB API audit', 'Выполнено', f'Проверено методов: {len(checks)}')
        _notify_in_app(state, 'WB API audit выполнен', 'Проверены токен, тарифы, склады, поставки и ограничения публичного API.', 'info')
        _save_state_sync(state)
        return state


def _coefficient_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('data', 'result', 'items', 'coefficients'):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def _norm_date(value: Any) -> str:
    if not value:
        return ''
    s = str(value)
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', s)
    if m:
        return m.group(1)
    m = re.search(r'(\d{2})\.(\d{2})\.(20\d{2})', s)
    if m:
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    return s[:10]


def _warehouse_matches(item: dict[str, Any], wanted: list[str]) -> bool:
    hay = ' '.join(str(item.get(k, '')) for k in ('warehouseName', 'warehouse', 'boxDeliveryWarehouseName', 'name', 'officeName')).lower()
    return any(w.lower() in hay for w in wanted)


async def wb_api_monitor_targets() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        cfg['search_source_mode'] = 'wb_first'
        cfg['postavleno_enabled'] = False
        cfg['wb_api_last_monitor_at'] = _now_iso()
        res = await _wb_http_probe('GET', 'https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients', cache_ttl_seconds=int(cfg.get('wb_api_cache_coefficients_seconds') or 900), min_delay_seconds=float(cfg.get('wb_api_request_min_delay_seconds') or 3))
        if not res.get('ok'):
            cfg['wb_api_last_error'] = res.get('message') or 'Не удалось получить коэффициенты WB'
            if res.get('rate_limited'):
                cfg['wb_api_status'] = 'rate_limited'
                _log(state, 'WB API мониторинг', 'Пауза из-за лимита WB', cfg['wb_api_last_error'])
                _notify_in_app(state, 'WB API ограничил частоту запросов', cfg['wb_api_last_error'], 'error')
            else:
                _log(state, 'WB API мониторинг', 'Ошибка', cfg['wb_api_last_error'])
                _notify_in_app(state, 'WB API мониторинг не выполнен', cfg['wb_api_last_error'], 'error')
            _save_state_sync(state)
            return state
        items = _coefficient_items(res.get('sample'))
        wanted_dates = {t['date']: t for t in state.get('targets', []) if t.get('status') not in {'booked', 'skipped_holiday'}}
        warehouses = cfg.get('warehouses') or []
        max_coef = int(cfg.get('max_coefficient') or 20)
        found = 0
        for item in items:
            d = _norm_date(item.get('date') or item.get('dateFrom') or item.get('deliveryDate') or item.get('acceptanceDate'))
            if d not in wanted_dates:
                continue
            if warehouses and not _warehouse_matches(item, warehouses):
                continue
            raw_coef = item.get('coefficient', item.get('deliveryCoef', item.get('storageCoef')))
            try:
                coef = int(float(str(raw_coef).replace(',', '.')))
            except Exception:
                coef = None
            allow = item.get('allowUnload')
            if coef is not None and coef <= max_coef and allow is not False:
                t = wanted_dates[d]
                t['status'] = 'found'
                t['status_label'] = _STATUS_LABELS['found']
                t['warehouse'] = item.get('warehouseName') or item.get('warehouse') or t.get('warehouse')
                t['coefficient'] = coef
                t['slot_time'] = item.get('time') or item.get('timeFrom') or t.get('slot_time') or 'требует выбора окна в ЛК WB'
                t['message'] = 'WB API: найдено подходящее условие приемки. Финальная бронь — через ЛК WB.'
                t['last_checked_at'] = _now_iso()
                found += 1
        cfg['wb_api_status'] = 'monitor_ok'
        _log(state, 'WB API мониторинг', 'Выполнено', f'Получено элементов: {len(items)}, совпадений по календарю: {found}' + (' (кэш)' if res.get('cached') else ''))
        _notify_in_app(state, 'WB API мониторинг выполнен', f'Совпадений по календарю: {found}. Если найдено окно — запускай бронь через ЛК WB.' + (' Данные из кэша WB на 15 минут.' if res.get('cached') else ''), 'success' if found else 'info')
        _save_state_sync(state)
        return state


async def _wb_page(alias: str = 'default'):
    global _WB_PLAYWRIGHT, _WB_CONTEXT, _WB_PAGE, _WB_ALIAS
    from playwright.async_api import async_playwright
    safe_alias = re.sub(r'[^a-zA-Z0-9_-]+', '_', alias or 'default')
    if _WB_CONTEXT is not None and _WB_PAGE is not None and _WB_ALIAS == safe_alias:
        try:
            if not _WB_PAGE.is_closed():
                return _WB_PAGE
        except Exception:
            pass
    try:
        if _WB_CONTEXT is not None:
            await _WB_CONTEXT.close()
    except Exception:
        pass
    try:
        if _WB_PLAYWRIGHT is not None:
            await _WB_PLAYWRIGHT.stop()
    except Exception:
        pass
    _WB_PLAYWRIGHT = await async_playwright().start()
    profile = WB_CABINET_PROFILE_BASE / safe_alias
    profile.mkdir(parents=True, exist_ok=True)
    _WB_CONTEXT = await _WB_PLAYWRIGHT.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=True,
        viewport={'width': 1440, 'height': 950},
        locale='ru-RU',
        args=['--no-sandbox', '--disable-dev-shm-usage'],
    )
    _WB_PAGE = _WB_CONTEXT.pages[0] if _WB_CONTEXT.pages else await _WB_CONTEXT.new_page()
    _WB_ALIAS = safe_alias
    return _WB_PAGE


async def _wb_save_screenshot(page) -> str:
    WB_CABINET_SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(WB_CABINET_SCREENSHOT), full_page=True)
    try:
        import base64
        return base64.b64encode(WB_CABINET_SCREENSHOT.read_bytes()).decode('ascii')
    except Exception:
        return ''


def _extract_wb_company(text: str) -> str:
    if not text:
        return ''
    # Ищем юрлицо в тексте шапки ЛК, например: ГОЛДСТАРТ ООО
    patterns = [r'([А-ЯЁA-Z0-9][А-ЯЁA-Z0-9 "«»\-]{2,60} (?:ООО|ИП|АО|ПАО))']
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return re.sub(r'\s+', ' ', m.group(1)).strip()
    return ''

async def _wb_login_state(page) -> tuple[bool, str]:
    text = ''
    try:
        text = await page.locator('body').inner_text(timeout=8000)
    except Exception:
        text = ''
    low = text.lower()
    login_markers = ['войти', 'логин', 'пароль', 'qr', 'код из sms', 'телефон', 'sign in', 'login']
    cabinet_markers = ['поставк', 'wildberries', 'seller', 'маркетплейс', 'продав']
    logged = any(m in low for m in cabinet_markers) and not ('код из sms' in low or 'пароль' in low and 'поставк' not in low)
    if any(m in low for m in login_markers) and not logged:
        return False, text[:700]
    return logged, text[:700]


async def wb_cabinet_status() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        state['wb_cabinet'] = {
            'status': cfg.get('wb_cabinet_connection_status') or 'not_connected',
            'profile_alias': cfg.get('wb_cabinet_profile_alias') or 'default',
            'last_action': cfg.get('wb_cabinet_last_action') or '',
            'screenshot_base64': cfg.get('wb_cabinet_last_screenshot') or '',
            'safe_mode': cfg.get('wb_cabinet_safe_mode', True),
        }
        return state


async def wb_cabinet_start_login(profile_alias: str = 'default') -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        cfg['wb_cabinet_profile_alias'] = profile_alias or 'default'
        cfg['wb_cabinet_connection_status'] = 'opening'
        try:
            page = await _wb_page(cfg['wb_cabinet_profile_alias'])
            await page.goto(cfg.get('wb_cabinet_url') or 'https://seller.wildberries.ru', wait_until='domcontentloaded', timeout=90000)
            await page.wait_for_timeout(4000)
            logged, hint = await _wb_login_state(page)
            cfg['wb_cabinet_detected_company'] = _extract_wb_company(hint)
            shot = await _wb_save_screenshot(page)
            cfg['wb_cabinet_last_screenshot'] = shot
            cfg['wb_cabinet_connection_status'] = 'connected' if logged else 'login_required'
            cfg['wb_cabinet_last_action'] = 'Открыт ЛК WB. Если видишь экран входа — авторизуйся в кабинете и нажми Проверить вход.'
            state['wb_cabinet'] = {'screenshot_base64': shot, 'body_hint': hint[:500]}
            _log(state, 'ЛК WB: открыть', cfg['wb_cabinet_connection_status'], cfg['wb_cabinet_last_action'])
        except Exception as exc:
            cfg['wb_cabinet_connection_status'] = 'error'
            cfg['wb_cabinet_last_action'] = str(exc)
            _log(state, 'ЛК WB: открыть', 'Ошибка', str(exc))
        _save_state_sync(state)
        return state


async def wb_cabinet_check_login(profile_alias: str = 'default') -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        cfg['wb_cabinet_profile_alias'] = profile_alias or cfg.get('wb_cabinet_profile_alias') or 'default'
        try:
            page = await _wb_page(cfg['wb_cabinet_profile_alias'])
            await page.goto(cfg.get('wb_cabinet_url') or 'https://seller.wildberries.ru', wait_until='domcontentloaded', timeout=90000)
            await page.wait_for_timeout(3000)
            logged, hint = await _wb_login_state(page)
            cfg['wb_cabinet_detected_company'] = _extract_wb_company(hint)
            shot = await _wb_save_screenshot(page)
            cfg['wb_cabinet_last_screenshot'] = shot
            cfg['wb_cabinet_connection_status'] = 'connected' if logged else 'login_required'
            cfg['wb_cabinet_last_action'] = 'ЛК WB авторизован' if logged else 'ЛК WB показывает экран входа/подтверждения'
            state['wb_cabinet'] = {'screenshot_base64': shot, 'body_hint': hint[:500]}
            _log(state, 'ЛК WB: проверка входа', cfg['wb_cabinet_connection_status'], cfg['wb_cabinet_last_action'])
        except Exception as exc:
            cfg['wb_cabinet_connection_status'] = 'error'
            cfg['wb_cabinet_last_action'] = str(exc)
            _log(state, 'ЛК WB: проверка входа', 'Ошибка', str(exc))
        _save_state_sync(state)
        return state



async def _wb_try_open_supplies_page(page, cfg: dict[str, Any]) -> str:
    """Open the most likely WB supplies page without using search or external sites."""
    urls = cfg.get('wb_cabinet_known_urls') or DEFAULT_SETTINGS['wb_cabinet_known_urls']
    last_text = ''
    for url in urls:
        try:
            if not str(url).startswith(('https://seller.wildberries.ru', 'https://seller.wb.ru')):
                continue
            await page.goto(url, wait_until='domcontentloaded', timeout=90000)
            await page.wait_for_timeout(4500)
            logged, hint = await _wb_login_state(page)
            try:
                last_text = await page.locator('body').inner_text(timeout=8000)
            except Exception:
                last_text = hint
            low = (last_text or '').lower()
            if logged and any(x in low for x in ['поставк', 'приёмк', 'приемк', 'чернов', 'слот', 'коэффициент', 'суперсейф']):
                return last_text
        except Exception as exc:
            last_text = str(exc)
    return last_text


def _cabinet_text_has_target_signal(text: str, target: dict[str, Any], cfg: dict[str, Any]) -> bool:
    low = (text or '').lower()
    target_date = target.get('date') or ''
    if not target_date:
        return False
    d = _parse_date(target_date)
    date_markers = [target_date, d.strftime('%d.%m.%Y'), d.strftime('%d.%m')]
    has_date = any(m and m.lower() in low for m in date_markers)
    has_wh = any(str(w).lower() in low for w in (cfg.get('warehouses') or []))
    has_flow = any(x in low for x in ['суперсейф', 'поставка', 'поставки', 'окно', 'слот', 'коэффициент', 'приемка', 'приёмка'])
    return bool(has_date and (has_wh or has_flow))


async def wb_cabinet_monitor_targets(profile_alias: str = 'default', manual: bool = False) -> dict[str, Any]:
    """Main FBO monitoring path via WB seller cabinet, not WB API."""
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        cfg['search_source_mode'] = 'wb_cabinet'
        cfg['postavleno_enabled'] = False
        cfg['wb_api_monitor_enabled'] = False
        cfg['wb_cabinet_monitor_last_at'] = _now_iso()
        cfg['wb_cabinet_profile_alias'] = profile_alias or cfg.get('wb_cabinet_profile_alias') or 'default'
        candidates = [t for t in state.get('targets', []) if t.get('status') not in {'booked', 'skipped_holiday'}]
        if not candidates:
            _log(state, 'ЛК WB мониторинг', 'Нет дат', 'Все целевые даты закрыты или исключены')
            _save_state_sync(state)
            return state
        candidate = candidates[0]
        try:
            page = await _wb_page(cfg['wb_cabinet_profile_alias'])
            await page.goto(cfg.get('wb_cabinet_url') or 'https://seller.wildberries.ru', wait_until='domcontentloaded', timeout=90000)
            await page.wait_for_timeout(3500)
            logged, hint = await _wb_login_state(page)
            if not logged:
                shot = await _wb_save_screenshot(page)
                cfg['wb_cabinet_last_screenshot'] = shot
                cfg['wb_cabinet_connection_status'] = 'login_required'
                cfg['wb_cabinet_last_action'] = 'ЛК WB не авторизован. Открой ЛК WB, войди и нажми Проверить вход.'
                candidate['status'] = 'error'
                candidate['status_label'] = _STATUS_LABELS['error']
                candidate['message'] = 'Нужна авторизация в ЛК WB для мониторинга окон.'
                _log(state, 'ЛК WB мониторинг', 'Нужен вход', cfg['wb_cabinet_last_action'], target_date=candidate.get('date'))
                _notify_in_app(state, 'Нужна авторизация в ЛК WB', 'Автопилот не может мониторить окна, пока кабинет не авторизован.', 'error')
                _save_state_sync(state)
                return state
            text = await _wb_try_open_supplies_page(page, cfg)
            shot = await _wb_save_screenshot(page)
            cfg['wb_cabinet_last_screenshot'] = shot
            cfg['wb_cabinet_connection_status'] = 'connected'
            cfg['wb_cabinet_last_action'] = 'ЛК WB открыт, страница поставок проверена в safe-mode.'
            found_count = 0
            for t in candidates[:5]:
                t['last_checked_at'] = _now_iso()
                t['attempts'] = int(t.get('attempts') or 0) + 1
                if _cabinet_text_has_target_signal(text, t, cfg):
                    t['status'] = 'found'
                    t['status_label'] = _STATUS_LABELS['found']
                    t['warehouse'] = t.get('warehouse') or '/'.join(cfg.get('warehouses') or [])
                    t['slot_time'] = t.get('slot_time') or 'видно в ЛК WB'
                    t['message'] = 'ЛК WB: дата/окно визуально найдено на странице поставок. Следующий шаг — создать/открыть черновик и привязать окно.'
                    found_count += 1
                elif t.get('status') in {'planned', 'searching', 'not_found', 'error'}:
                    t['status'] = 'searching'
                    t['status_label'] = _STATUS_LABELS['searching']
                    t['message'] = 'ЛК WB проверен, явного совпадения по дате/окну пока нет. Автопилот повторит проверку по расписанию.'
            _log(state, 'ЛК WB мониторинг', 'Выполнено', f'Проверено ближайших дат: {min(len(candidates),5)}, найдено сигналов: {found_count}', target_date=candidate.get('date'))
            if found_count:
                _notify_in_app(state, 'ЛК WB: найдено возможное окно', f'Найдено совпадений: {found_count}. Проверь скриншот, затем запускай подготовку брони.', 'success')
            elif manual:
                _notify_in_app(state, 'ЛК WB мониторинг выполнен', 'Окна по целевым датам пока не найдены. Автопилот продолжит работу по расписанию.', 'info')
        except Exception as exc:
            cfg['wb_cabinet_connection_status'] = 'error'
            cfg['wb_cabinet_last_action'] = str(exc)
            candidate['status'] = 'error'
            candidate['status_label'] = _STATUS_LABELS['error']
            candidate['message'] = str(exc)
            _log(state, 'ЛК WB мониторинг', 'Ошибка', str(exc), target_date=candidate.get('date'))
            _notify_in_app(state, 'Ошибка мониторинга ЛК WB', str(exc), 'error')
        _save_state_sync(state)
        return state


_RU_MONTHS_GEN = {
    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля', 5: 'мая', 6: 'июня',
    7: 'июля', 8: 'августа', 9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
}

async def _wb_route_shot(page, cfg: dict[str, Any], step: str, step_index: int | None = None, total_steps: int = 15) -> str:
    cfg['wb_cabinet_last_route_step'] = step
    cfg['wb_cabinet_last_action'] = step
    shot = await _wb_save_screenshot(page)
    _WB_ROUTE_PROGRESS.update({
        'running': bool(_WB_ROUTE_PROGRESS.get('running')),
        'step': step,
        'step_index': step_index if step_index is not None else int(_WB_ROUTE_PROGRESS.get('step_index') or 0),
        'total_steps': total_steps,
        'last_updated_at': _now_iso(),
        'screenshot_base64': shot,
        'error': '',
    })
    return shot

async def _wb_route_check_stop() -> None:
    if _WB_ROUTE_PROGRESS.get('stop_requested'):
        raise RuntimeError('Маршрут остановлен пользователем')

async def wb_cabinet_live_status() -> dict[str, Any]:
    state = _load_state_sync()
    cfg = state.get('settings') or DEFAULT_SETTINGS.copy()
    progress = dict(_WB_ROUTE_PROGRESS)
    if not progress.get('screenshot_base64'):
        progress['screenshot_base64'] = cfg.get('wb_cabinet_last_screenshot') or ''
    progress['connection_status'] = cfg.get('wb_cabinet_connection_status') or 'not_connected'
    progress['profile_alias'] = cfg.get('wb_cabinet_profile_alias') or 'default'
    progress['last_action'] = cfg.get('wb_cabinet_last_action') or ''
    progress['expected_company'] = cfg.get('wb_cabinet_expected_company') or ''
    progress['detected_company'] = cfg.get('wb_cabinet_detected_company') or ''
    return {'ok': True, 'live': progress}

async def wb_cabinet_stop_route() -> dict[str, Any]:
    _WB_ROUTE_PROGRESS['stop_requested'] = True
    _WB_ROUTE_PROGRESS['last_updated_at'] = _now_iso()
    return await wb_cabinet_live_status()

async def _wb_click_any(page, labels: list[str], *, exact: bool = True, timeout: int = 7000) -> str:
    last_error = ''
    for label in labels:
        for mode in ('role', 'exact', 'regex'):
            try:
                if mode == 'role':
                    loc = page.get_by_role('button', name=re.compile(re.escape(label), re.I)).first
                elif mode == 'exact':
                    loc = page.get_by_text(label, exact=exact).first
                else:
                    loc = page.get_by_text(re.compile(re.escape(label), re.I)).first
                if await loc.count():
                    await loc.click(timeout=timeout)
                    await page.wait_for_timeout(1200)
                    return label
            except Exception as exc:
                last_error = str(exc)
    raise RuntimeError(f'Не нашла кнопку/текст: {labels}. Последняя ошибка: {last_error}')

async def _wb_fill_first_quantity(page, quantity: int) -> str:
    clicked = False
    for sel in ['table tbody tr:first-child input[type="checkbox"]', 'input[type="checkbox"]', '[role="row"] input[type="checkbox"]']:
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.click(timeout=5000)
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        for sel in ['table tbody tr:first-child', '[role="row"]:nth-child(2)', '[data-testid*="product"]']:
            try:
                loc = page.locator(sel).first
                if await loc.count():
                    await loc.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                pass
    await page.wait_for_timeout(900)
    for sel in ['input[type="number"]', 'input[inputmode="numeric"]', '[role="spinbutton"]', 'input']:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(min(count, 14)):
                inp = locs.nth(i)
                try:
                    box = await inp.bounding_box(timeout=1000)
                    if not box or box.get('width', 0) < 20 or box.get('height', 0) < 15:
                        continue
                    ph = ''
                    try:
                        ph = (await inp.get_attribute('placeholder')) or ''
                    except Exception:
                        pass
                    if any(x in ph.lower() for x in ['поиск', 'search', 'артикул', 'barcode', 'баркод']):
                        continue
                    await inp.click(timeout=2500)
                    await inp.fill(str(quantity), timeout=4000)
                    await page.wait_for_timeout(800)
                    return f'выбран первый товар, количество {quantity}'
                except Exception:
                    continue
        except Exception:
            continue
    raise RuntimeError('Не удалось найти поле количества для первого товара')

async def _wb_confirm_manual_product_selection(page) -> None:
    for labels in [['Добавить', 'Выбрать', 'Применить', 'Сохранить'], ['Готово', 'Продолжить']]:
        try:
            await _wb_click_any(page, labels, exact=False, timeout=2500)
            await page.wait_for_timeout(1500)
            return
        except Exception:
            pass

async def _wb_select_dropdown_value(page, current_or_label: str, value: str) -> None:
    body = ''
    try:
        body = await page.locator('body').inner_text(timeout=5000)
    except Exception:
        body = ''
    if value.lower() in body.lower():
        return
    try:
        await _wb_click_any(page, [current_or_label], exact=False, timeout=4000)
    except Exception:
        try:
            await page.get_by_text(re.compile(re.escape(current_or_label), re.I)).first.click(timeout=3000)
        except Exception:
            pass
    await page.wait_for_timeout(1000)
    await _wb_click_any(page, [value], exact=False, timeout=7000)

async def _wb_select_warehouse_and_type(page, warehouse: str, supply_type: str) -> None:
    await _wb_select_dropdown_value(page, 'Склад назначения', warehouse)
    try:
        await _wb_select_dropdown_value(page, 'Транзитный склад', 'Без транзитного склада')
    except Exception:
        pass
    await _wb_click_any(page, [supply_type or 'Суперсейф', 'Суперсейф'], exact=False, timeout=7000)

async def _wb_select_date_in_calendar(page, iso_date: str) -> str:
    d = _parse_date(iso_date)
    variants = [d.strftime('%d.%m.%Y'), d.strftime('%d.%m'), f'{d.day} {_RU_MONTHS_GEN[d.month]}', f'{d.day} {_RU_MONTHS_GEN[d.month][:3]}']
    last_body = ''
    for attempt in range(8):
        try:
            last_body = await page.locator('body').inner_text(timeout=6000)
        except Exception:
            last_body = ''
        for marker in variants:
            if marker and marker.lower() in last_body.lower():
                try:
                    card = page.locator('div, section, article').filter(has_text=re.compile(re.escape(marker), re.I)).filter(has_text=re.compile('Выбрать', re.I)).first
                    if await card.count():
                        await card.get_by_text(re.compile('Выбрать', re.I)).first.click(timeout=6000)
                        await page.wait_for_timeout(2000)
                        return marker
                except Exception:
                    pass
        for sel in ['button[aria-label*="next" i]', 'button[aria-label*="след" i]']:
            try:
                loc = page.locator(sel).first
                if await loc.count():
                    await loc.click(timeout=2500)
                    await page.wait_for_timeout(1200)
                    break
            except Exception:
                pass
        else:
            try:
                await page.keyboard.press('PageDown')
                await page.wait_for_timeout(1200)
            except Exception:
                pass
    raise RuntimeError(f'Не нашла целевую дату {iso_date} на календаре. Последний текст экрана: {last_body[:500]}')

async def _wb_next(page) -> None:
    await _wb_click_any(page, ['Дальше', 'Далее', 'Продолжить'], exact=False, timeout=10000)

async def _wb_run_supply_creation_flow(page, cfg: dict[str, Any], target: dict[str, Any], *, real_booking: bool) -> dict[str, Any]:
    target_date = target.get('date')
    warehouse = target.get('warehouse') or cfg.get('wb_cabinet_default_warehouse') or (cfg.get('warehouses') or ['Электросталь'])[0]
    if warehouse not in (cfg.get('warehouses') or []):
        warehouse = (cfg.get('warehouses') or ['Электросталь'])[0]
    quantity = int(cfg.get('wb_cabinet_template_quantity') or 1000)
    supply_type = cfg.get('supply_type') or 'Суперсейф'
    steps: list[str] = []
    await page.goto('https://seller.wildberries.ru/supplies-management/all-supplies', wait_until='domcontentloaded', timeout=90000)
    await page.wait_for_timeout(4500)
    steps.append('открыт раздел поставок')
    await _wb_route_shot(page, cfg, '1/15 Открыт раздел поставок', 1); await _wb_route_check_stop()
    try:
        await _wb_click_any(page, ['Черновики'], exact=False, timeout=8000)
        steps.append('открыта вкладка Черновики')
    except Exception:
        steps.append('вкладка Черновики не найдена, продолжаю с текущей страницы')
    await page.wait_for_timeout(1200)
    await _wb_click_any(page, ['Новая поставка', '+ Новая поставка'], exact=False, timeout=12000)
    steps.append('нажата Новая поставка')
    await page.wait_for_timeout(2500)
    await _wb_route_shot(page, cfg, '3/15 Нажата Новая поставка', 3); await _wb_route_check_stop()
    await _wb_click_any(page, ['Выбрать из списка'], exact=False, timeout=12000)
    steps.append('выбран ручной способ')
    await page.wait_for_timeout(2500)
    await _wb_route_shot(page, cfg, '4/15 Выбор товаров вручную', 4); await _wb_route_check_stop()
    steps.append(await _wb_fill_first_quantity(page, quantity))
    await _wb_confirm_manual_product_selection(page)
    await _wb_route_shot(page, cfg, '6/15 Первый товар выбран, количество проставлено', 6); await _wb_route_check_stop()
    await _wb_next(page)
    steps.append('переход к складу и типу поставки')
    await page.wait_for_timeout(3500)
    await _wb_select_warehouse_and_type(page, warehouse, supply_type)
    steps.append(f'выбран склад {warehouse}, тип {supply_type}, транзит без склада')
    await _wb_route_shot(page, cfg, '9/15 Склад и тип поставки выбраны', 9); await _wb_route_check_stop()
    await _wb_next(page)
    await page.wait_for_timeout(3500)
    selected_marker = await _wb_select_date_in_calendar(page, target_date)
    steps.append(f'выбрана дата {target_date} ({selected_marker})')
    target['warehouse'] = warehouse
    target['slot_time'] = target.get('slot_time') or 'дата выбрана в ЛК WB'
    await _wb_route_shot(page, cfg, '11/15 Дата поставки выбрана', 11); await _wb_route_check_stop()
    try:
        await _wb_next(page)
        steps.append('переход с даты к упаковке/ШК')
        await page.wait_for_timeout(2500)
    except Exception:
        pass
    await _wb_route_shot(page, cfg, '12/15 Упаковка и печать ШК — без заполнения', 12); await _wb_route_check_stop()
    try:
        await _wb_next(page)
        steps.append('этап упаковки/ШК пропущен')
        await page.wait_for_timeout(3000)
    except Exception as exc:
        steps.append(f'не удалось нажать Дальше на упаковке/ШК: {str(exc)[:120]}')
    await _wb_route_shot(page, cfg, '14/15 Финальный экран пропуска и ШК поставки', 14); await _wb_route_check_stop()
    body = ''
    try:
        body = await page.locator('body').inner_text(timeout=8000)
    except Exception:
        body = ''
    m = re.search(r'(?:Новая\s+)?поставка\s*№\s*([0-9]+)', body, flags=re.I)
    supply_no = m.group(1) if m else None
    if not real_booking:
        return {'ok': True, 'booked': False, 'supply_no': supply_no, 'warehouse': warehouse, 'steps': steps, 'message': 'Тестовый режим: дошли до финального экрана, кнопку “Создать поставку” не нажимали.'}
    expected_company = str(cfg.get('wb_cabinet_expected_company') or '').strip()
    detected_company = _extract_wb_company(body) or str(cfg.get('wb_cabinet_detected_company') or '').strip()
    cfg['wb_cabinet_detected_company'] = detected_company
    confirmation = str(cfg.get('wb_cabinet_final_confirmation_text') or '').strip()
    if cfg.get('wb_cabinet_require_final_confirmation', True):
        required = f'Создавать поставки в {expected_company}' if expected_company else 'Создавать поставки'
        if confirmation != required:
            return {'ok': True, 'booked': False, 'supply_no': supply_no, 'warehouse': warehouse, 'steps': steps, 'message': f'Боевой режим заблокирован: для финального клика введи подтверждение: {required}'}
    if expected_company and detected_company and expected_company.lower() not in detected_company.lower():
        return {'ok': True, 'booked': False, 'supply_no': supply_no, 'warehouse': warehouse, 'steps': steps, 'message': f'Боевой режим заблокирован: ожидаемый ЛК {expected_company}, на экране распознан {detected_company}'}
    await _wb_click_any(page, ['Создать поставку'], exact=False, timeout=12000)
    steps.append('нажата Создать поставку')
    await page.wait_for_timeout(6500)
    await _wb_route_shot(page, cfg, '15/15 После создания поставки', 15)
    try:
        body = await page.locator('body').inner_text(timeout=8000)
    except Exception:
        body = ''
    m = re.search(r'(?:поставка|заказ)\s*№\s*([0-9]+)', body, flags=re.I)
    supply_no = (m.group(1) if m else supply_no) or 'создана, номер не распознан'
    return {'ok': True, 'booked': True, 'supply_no': supply_no, 'warehouse': warehouse, 'steps': steps, 'message': f'Поставка создана в ЛК WB. Номер: {supply_no}'}

async def wb_cabinet_book_next_safe(profile_alias: str = 'default') -> dict[str, Any]:
    _WB_ROUTE_PROGRESS.update({'running': True, 'stop_requested': False, 'step': 'Старт маршрута создания поставки', 'step_index': 0, 'total_steps': 15, 'mode': 'real' if False else 'test', 'last_updated_at': _now_iso(), 'error': ''})
    async with _lock:
        state = _load_state_sync()
        _upsert_targets(state)
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        candidate = next((t for t in state.get('targets', []) if t.get('status') in {'found', 'planned', 'searching', 'not_found'}), None)
        if not candidate:
            _log(state, 'ЛК WB: бронь', 'Нет дат', 'Нет незакрытых дат для бронирования')
            _save_state_sync(state)
            return state
        cfg['wb_cabinet_profile_alias'] = profile_alias or cfg.get('wb_cabinet_profile_alias') or 'default'
        real_booking = bool(cfg.get('wb_cabinet_real_booking_enabled')) and not bool(cfg.get('wb_cabinet_safe_mode', True))
        mode_label = 'боевой режим' if real_booking else 'тестовый режим'
        _WB_ROUTE_PROGRESS['mode'] = 'real' if real_booking else 'test'
        try:
            page = await _wb_page(cfg['wb_cabinet_profile_alias'])
            await page.goto(cfg.get('wb_cabinet_url') or 'https://seller.wildberries.ru', wait_until='domcontentloaded', timeout=90000)
            await page.wait_for_timeout(2500)
            logged, hint = await _wb_login_state(page)
            if not logged:
                shot = await _wb_save_screenshot(page)
                cfg['wb_cabinet_last_screenshot'] = shot
                cfg['wb_cabinet_connection_status'] = 'login_required'
                candidate['status'] = 'error'
                candidate['status_label'] = _STATUS_LABELS['error']
                candidate['message'] = 'Нужна авторизация в ЛК WB'
                _log(state, 'ЛК WB: создание поставки', 'Нужен вход', 'ЛК WB показывает экран входа/подтверждения', target_date=candidate.get('date'))
                _notify_in_app(state, 'Нужна авторизация в ЛК WB', 'Войди в кабинет WB и нажми “Проверить вход”.', 'error')
                _save_state_sync(state)
                return state
            candidate['status'] = 'booking'
            candidate['status_label'] = _STATUS_LABELS['booking']
            candidate['message'] = f'ЛК WB: запущен маршрут создания поставки ({mode_label}).'
            _save_state_sync(state)
            result = await _wb_run_supply_creation_flow(page, cfg, candidate, real_booking=real_booking)
            shot = await _wb_save_screenshot(page)
            cfg['wb_cabinet_last_screenshot'] = shot
            cfg['wb_cabinet_connection_status'] = 'connected'
            cfg['wb_cabinet_last_action'] = result.get('message') or 'Маршрут создания поставки выполнен'
            candidate['last_checked_at'] = _now_iso()
            candidate['warehouse'] = result.get('warehouse') or candidate.get('warehouse')
            if result.get('supply_no'):
                candidate['draft_id'] = str(result.get('supply_no'))
            if result.get('booked'):
                candidate['status'] = 'booked'
                candidate['status_label'] = _STATUS_LABELS['booked']
                candidate['message'] = f"Поставка создана и дата выбрана. Склад: {candidate.get('warehouse')}. Номер: {candidate.get('draft_id')}."
                _log(state, 'ЛК WB: создание поставки', 'Забронировано', ' → '.join(result.get('steps') or []), target_date=candidate.get('date'), warehouse=candidate.get('warehouse'), draft_id=candidate.get('draft_id'))
                _notify_in_app(state, 'WB FBO: поставка создана', candidate['message'], 'success')
                try:
                    await _send_telegram_message(f"✅ WB FBO: поставка создана\nДата: {candidate.get('date')}\nСклад: {candidate.get('warehouse')}\nТип: {cfg.get('supply_type')}\nНомер: {candidate.get('draft_id')}")
                except Exception:
                    pass
            else:
                candidate['status'] = 'found'
                candidate['status_label'] = _STATUS_LABELS['found']
                candidate['message'] = result.get('message') or 'Тестовый режим: маршрут прошел до финального экрана без создания поставки.'
                _log(state, 'ЛК WB: тестовый маршрут', 'Готово к бою', ' → '.join(result.get('steps') or []), target_date=candidate.get('date'), warehouse=candidate.get('warehouse'), draft_id=candidate.get('draft_id'))
                _notify_in_app(state, 'WB FBO: тестовый маршрут выполнен', f"Дата {candidate.get('date')}, склад {candidate.get('warehouse')}. Финальная кнопка не нажата.", 'info')
        except Exception as exc:
            candidate['status'] = 'error'
            candidate['status_label'] = _STATUS_LABELS['error']
            candidate['message'] = str(exc)
            cfg['wb_cabinet_connection_status'] = 'error'
            cfg['wb_cabinet_last_action'] = str(exc)
            try:
                page = await _wb_page(cfg['wb_cabinet_profile_alias'])
                cfg['wb_cabinet_last_screenshot'] = await _wb_save_screenshot(page)
            except Exception:
                pass
            _log(state, 'ЛК WB: создание поставки', 'Ошибка', str(exc), target_date=candidate.get('date'))
            _notify_in_app(state, 'Ошибка маршрута ЛК WB', str(exc), 'error')
            _WB_ROUTE_PROGRESS.update({'error': str(exc), 'running': False, 'last_updated_at': _now_iso()})
        _WB_ROUTE_PROGRESS.update({'running': False, 'last_updated_at': _now_iso()})
        _save_state_sync(state)
        return state


async def full_wb_first_tick() -> dict[str, Any]:
    return await wb_first_autopilot_tick(manual=True)


async def postavleno_disabled_state() -> dict[str, Any]:
    async with _lock:
        state = _load_state_sync()
        cfg = state.setdefault('settings', DEFAULT_SETTINGS.copy())
        cfg['postavleno_enabled'] = False
        cfg['postavleno_connection_status'] = 'disabled_wb_first'
        cfg['telegram_web_status'] = 'disabled_wb_first'
        cfg['telegram_web_last_error'] = 'POSTAVLENO Web отключен в WB-first сборке: чтобы не читать личные чаты и не писать не туда.'
        _log(state, 'POSTAVLENO', 'Отключено', cfg['telegram_web_last_error'])
        _notify_in_app(state, 'POSTAVLENO отключен', 'В этой сборке хаб работает от WB API/ЛК WB. POSTAVLENO можно использовать только вручную как внешний сигнал.', 'info')
        _save_state_sync(state)
        return state
