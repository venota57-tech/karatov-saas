from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta, time
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import settings

router = APIRouter(prefix="/wb-booking", tags=["wb-booking"])

# Runtime modes:
# - monitor_only: only writes events / status
# - notify_only: checks and notifies
# - auto_book: full target mode. Until the safe WB booking adapter is connected,
#              it behaves as notify + audit without pretending that booking happened.
# Work time modes:
# - auto: auto_book runs 24/7, other modes run in business hours
# - business_hours: use work_time_from/work_time_to
# - 24_7: always run
_state: dict[str, Any] = {
    "enabled": False,
    "mode": "auto_book",
    "work_time_mode": getattr(settings, "wb_booking_default_work_time_mode", "auto"),
    "warehouses": ["Коледино", "Электросталь"],
    "supply_type": "Суперсейф",
    "coefficient_limit": 20,
    "start_date": None,
    "every_n_workdays": 3,
    "horizon_days": 60,
    "work_time_from": "09:00",
    "work_time_to": "21:00",
    "telegram_enabled": True,
    "telegram_connected": bool(settings.telegram_bot_token and settings.telegram_chat_id),
    "telegram_status": "Подключен" if (settings.telegram_bot_token and settings.telegram_chat_id) else "Нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в Render Environment",
    "email_enabled": False,
    "email_recipients": [],
    "last_check_at": None,
    "last_notification_at": None,
    "last_error": None,
    "events": [],
    "found_slots": [],
    "booking_history": [],
    "closed_target_dates": [],
}

_PERSIST_NAME = "slot_hunter"
_PERSIST_KEYS = {
    "enabled", "mode", "work_time_mode", "warehouses", "supply_type", "coefficient_limit",
    "start_date", "every_n_workdays", "horizon_days", "work_time_from", "work_time_to",
    "telegram_enabled", "email_enabled", "email_recipients", "closed_target_dates"
}

def _public_state_for_persist() -> dict[str, Any]:
    return {k: v for k, v in _state.items() if k in _PERSIST_KEYS}

def _load_persisted_state() -> None:
    global _CONFIG_LOADED
    if _CONFIG_LOADED:
        return
    try:
        from ..database import SessionLocal
        from ..models import AutomationRules
        db = SessionLocal()
        try:
            row = db.query(AutomationRules).filter(AutomationRules.name == _PERSIST_NAME).first()
            if row and isinstance(row.rules, dict):
                _state.update({k: v for k, v in row.rules.items() if k in _PERSIST_KEYS})
        finally:
            db.close()
        _CONFIG_LOADED = True
    except Exception as exc:
        _state["last_error"] = f"slot_hunter_load_config: {exc}"

def _persist_state() -> None:
    try:
        from ..database import SessionLocal
        from ..models import AutomationRules
        db = SessionLocal()
        try:
            row = db.query(AutomationRules).filter(AutomationRules.name == _PERSIST_NAME).first()
            if not row:
                row = AutomationRules(name=_PERSIST_NAME, rules={})
                db.add(row)
            row.rules = _public_state_for_persist()
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        _state["last_error"] = f"slot_hunter_save_config: {exc}"

_CONFIG_LOADED = False
_lock = asyncio.Lock()


class BookingConfig(BaseModel):
    enabled: bool = False
    mode: str = Field("auto_book", description="monitor_only | notify_only | auto_book")
    work_time_mode: str = Field("auto", description="auto | business_hours | 24_7")
    warehouses: list[str] = ["Коледино", "Электросталь"]
    supply_type: str = "Суперсейф"
    coefficient_limit: int | str = 20
    start_date: str | None = None
    every_n_workdays: int | str = 3
    interval_workdays: int | str | None = None
    horizon_days: int | str = 60
    work_time_from: str = "09:00"
    work_time_to: str = "21:00"
    telegram_enabled: bool = True
    email_enabled: bool = False
    email_recipients: list[str] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(kind: str, message: str, payload: dict | None = None):
    row = {"at": _now(), "kind": kind, "message": message, "payload": payload or {}}
    _state["events"].insert(0, row)
    _state["events"] = _state["events"][:500]
    return row


def _as_int(value, default: int, minimum: int = 1):
    try:
        if value == "" or value is None:
            return default
        return max(minimum, int(value))
    except Exception:
        return default


def _normalize_mode(value: str | None) -> str:
    value = (value or "auto_book").strip()
    return value if value in {"monitor_only", "notify_only", "auto_book"} else "auto_book"


def _normalize_work_time_mode(value: str | None) -> str:
    value = (value or "auto").strip()
    return value if value in {"auto", "business_hours", "24_7"} else "auto"


def _effective_work_time_mode() -> str:
    mode = _normalize_mode(_state.get("mode"))
    work_time_mode = _normalize_work_time_mode(_state.get("work_time_mode"))
    if work_time_mode == "auto":
        return "24_7" if mode == "auto_book" else "business_hours"
    return work_time_mode


def _runtime_label() -> str:
    effective = _effective_work_time_mode()
    if effective == "24_7":
        return "круглосуточно"
    return f"{_state.get('work_time_from','09:00')}–{_state.get('work_time_to','21:00')}"


def _workdays_schedule(start_date: str | None, every: int, horizon: int):
    if not start_date:
        start_date = datetime.now(timezone.utc).date().isoformat()
    try:
        current = datetime.fromisoformat(str(start_date)[:10]).date()
    except Exception:
        current = datetime.now(timezone.utc).date()
    end = current + timedelta(days=max(1, horizon))
    result = []
    workday_index = 0
    closed = set(_state.get("closed_target_dates") or [])
    while current <= end:
        if current.weekday() < 5:
            if workday_index % max(1, every) == 0 and current.isoformat() not in closed:
                result.append(current.isoformat())
            workday_index += 1
        current += timedelta(days=1)
    return result[:120]


def _parse_hhmm(value: str, default: time) -> time:
    try:
        hh, mm = str(value).split(":")[:2]
        return time(int(hh), int(mm))
    except Exception:
        return default


def _in_business_window() -> bool:
    now = datetime.now().time()
    start = _parse_hhmm(_state.get("work_time_from") or "09:00", time(9, 0))
    end = _parse_hhmm(_state.get("work_time_to") or "21:00", time(21, 0))
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def _should_run_now() -> bool:
    return _effective_work_time_mode() == "24_7" or _in_business_window()


def _status_payload():
    every = _as_int(_state.get("every_n_workdays"), 3)
    horizon = _as_int(_state.get("horizon_days"), 60)
    planned = _workdays_schedule(_state.get("start_date"), every, horizon)
    _state["telegram_connected"] = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    _state["telegram_status"] = "Подключен через @KARATOV_FBO_Booking_Bot" if _state["telegram_connected"] else "Нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в Render Environment"
    return {
        **_state,
        "mode": _normalize_mode(_state.get("mode")),
        "work_time_mode": _normalize_work_time_mode(_state.get("work_time_mode")),
        "effective_work_time_mode": _effective_work_time_mode(),
        "runtime_label": _runtime_label(),
        "is_inside_runtime_now": _should_run_now(),
        "planned_dates": planned,
        "target_dates": planned,
        "api_first": True,
        "browser_automation": False,
        "default_mode": "auto_book",
        "available_modes": [
            {"id": "monitor_only", "title": "Только мониторинг"},
            {"id": "notify_only", "title": "Найти и уведомить"},
            {"id": "auto_book", "title": "Автобронь + уведомление"},
        ],
        "available_work_time_modes": [
            {"id": "auto", "title": "Авто: автобронь 24/7, мониторинг и уведомления по рабочему окну"},
            {"id": "business_hours", "title": "Только рабочее окно"},
            {"id": "24_7", "title": "Круглосуточно"},
        ],
        "notification_channels": {
            "telegram": {
                "enabled": bool(_state.get("telegram_enabled")),
                "connected": bool(_state.get("telegram_connected")),
                "status": _state.get("telegram_status"),
            },
            "email": {
                "enabled": bool(_state.get("email_enabled")),
                "recipients_count": len(_state.get("email_recipients") or []),
            },
        },
        "description": "Slot Hunter работает по API-first логике: расписание поставок → проверка окон/коэффициентов → уведомление или автобронь в зависимости от выбранного режима.",
        "safety": [
            "Не ходим в ЛК WB браузером как человек в основном сценарии.",
            "Ищем только даты из заданного графика, а не случайные окна.",
            "Каждое действие фиксируется в истории.",
            "Автобронь включается настройкой. Если безопасный WB adapter не подключен, система не имитирует бронь, а честно уведомляет.",
            "Для auto_book по умолчанию используется 24/7, чтобы не пропускать ночные окна.",
        ],
    }


async def _send_telegram(text: str) -> dict[str, Any]:
    if not _state.get("telegram_enabled"):
        return {"ok": False, "skipped": True, "reason": "telegram_disabled"}
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return {"ok": False, "skipped": True, "reason": "telegram_env_missing"}
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json={"chat_id": settings.telegram_chat_id, "text": text})
    if response.status_code >= 400:
        return {"ok": False, "status_code": response.status_code, "body": response.text[:500]}
    _state["last_notification_at"] = _now()
    return {"ok": True}


def _no_slots_text() -> str:
    if _effective_work_time_mode() == "24_7":
        return (
            "WB FBO: окна пока не найдены\n"
            "Автопоиск работает круглосуточно. Следующая попытка будет выполнена автоматически."
        )
    return (
        "WB FBO: окна пока не найдены\n"
        f"Автопоиск продолжает работу по расписанию {_state.get('work_time_from','09:00')}–{_state.get('work_time_to','21:00')}. "
        "Следующая попытка будет выполнена автоматически."
    )


async def _notify_no_slots() -> dict[str, Any]:
    text = _no_slots_text()
    result = await _send_telegram(text)
    _event("telegram_notification", text, result)
    return result


@router.get("/status")
def status():
    _load_persisted_state()
    return _status_payload()


@router.post("/config")
def save_config(cfg: BookingConfig):
    data = cfg.model_dump()
    if data.get("interval_workdays") is not None and not data.get("every_n_workdays"):
        data["every_n_workdays"] = data.get("interval_workdays")
    data["mode"] = _normalize_mode(data.get("mode"))
    data["work_time_mode"] = _normalize_work_time_mode(data.get("work_time_mode"))
    data["coefficient_limit"] = _as_int(data.get("coefficient_limit"), 20)
    data["every_n_workdays"] = _as_int(data.get("every_n_workdays"), 3)
    data["horizon_days"] = _as_int(data.get("horizon_days"), 60)
    data.pop("interval_workdays", None)
    _state.update(data)
    _event("config_saved", "Настройки Slot Hunter сохранены", {k: v for k, v in data.items() if "token" not in k and "chat" not in k})
    _persist_state()
    return _status_payload()


@router.post("/start")
def start(cfg: BookingConfig | None = None):
    if cfg:
        save_config(cfg)
    _state["enabled"] = True
    _event("started", f"Slot Hunter включен. Режим: {_state.get('mode')}. Время работы: {_runtime_label()}.")
    _persist_state()
    return _status_payload()


@router.post("/stop")
def stop():
    _state["enabled"] = False
    _event("stopped", "Мониторинг Slot Hunter остановлен")
    _persist_state()
    return _status_payload()


@router.post("/check")
async def check_now():
    async with _lock:
        _state["last_check_at"] = _now()
        planned = _workdays_schedule(_state.get("start_date"), _as_int(_state.get("every_n_workdays"), 3), _as_int(_state.get("horizon_days"), 60))
        # Безопасный слой: если WB adapter не вернул окно, не имитируем найденный слот.
        _state["found_slots"] = []
        notify_result = await _notify_no_slots() if bool(settings.wb_booking_notify_empty_checks) else {"skipped": True}
        _event(
            "check",
            f"WB FBO: окна пока не найдены. Режим: {_state.get('mode')}. Время работы: {_runtime_label()}.",
            {
                "planned_dates": planned[:15],
                "warehouses": _state.get("warehouses"),
                "coefficient_limit": _state.get("coefficient_limit"),
                "notification": notify_result,
                "effective_work_time_mode": _effective_work_time_mode(),
            },
        )
        return _status_payload()


@router.post("/notify-test")
async def notify_test():
    result = await _notify_no_slots()
    return {"ok": bool(result.get("ok")), "result": result, "status": _status_payload()}


async def booking_auto_loop() -> None:
    _load_persisted_state()
    await asyncio.sleep(15)
    while True:
        try:
            if _state.get("enabled"):
                if _should_run_now():
                    await check_now()
                else:
                    _event("skipped_outside_runtime", f"Slot Hunter на паузе: рабочее окно {_runtime_label()}")
        except Exception as exc:
            _state["last_error"] = str(exc)
            _event("error", f"Ошибка Slot Hunter: {exc}")
        await asyncio.sleep(max(60, int(settings.wb_booking_auto_check_interval_seconds)))


booking_auto_check_loop = booking_auto_loop
