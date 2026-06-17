from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta, time
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import settings

router = APIRouter(prefix="/wb-booking", tags=["wb-booking"])

_state: dict[str, Any] = {
    "enabled": False,
    "mode": "monitor_only",
    "warehouses": ["Коледино", "Электросталь"],
    "supply_type": "Суперсейф",
    "coefficient_limit": 20,
    "start_date": None,
    "every_n_workdays": 3,
    "horizon_days": 30,
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
}

_lock = asyncio.Lock()

class BookingConfig(BaseModel):
    enabled: bool = False
    mode: str = Field("monitor_only", description="monitor_only | notify_only | auto_book")
    warehouses: list[str] = ["Коледино", "Электросталь"]
    supply_type: str = "Суперсейф"
    coefficient_limit: int | str = 20
    start_date: str | None = None
    every_n_workdays: int | str = 3
    interval_workdays: int | str | None = None
    horizon_days: int | str = 30
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
    _state["events"] = _state["events"][:300]
    return row


def _as_int(value, default: int, minimum: int = 1):
    try:
        if value == "" or value is None:
            return default
        return max(minimum, int(value))
    except Exception:
        return default


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
    while current <= end:
        if current.weekday() < 5:
            if workday_index % max(1, every) == 0:
                result.append(current.isoformat())
            workday_index += 1
        current += timedelta(days=1)
    return result[:80]


def _parse_hhmm(value: str, default: time) -> time:
    try:
        hh, mm = str(value).split(":")[:2]
        return time(int(hh), int(mm))
    except Exception:
        return default


def _in_work_window() -> bool:
    now = datetime.now().time()
    start = _parse_hhmm(_state.get("work_time_from") or "09:00", time(9, 0))
    end = _parse_hhmm(_state.get("work_time_to") or "21:00", time(21, 0))
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def _status_payload():
    every = _as_int(_state.get("every_n_workdays"), 3)
    horizon = _as_int(_state.get("horizon_days"), 30)
    planned = _workdays_schedule(_state.get("start_date"), every, horizon)
    _state["telegram_connected"] = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    _state["telegram_status"] = "Подключен через @KARATOV_FBO_Booking_Bot" if _state["telegram_connected"] else "Нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в Render Environment"
    return {
        **_state,
        "planned_dates": planned,
        "target_dates": planned,
        "api_first": True,
        "browser_automation": False,
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
        "description": "Slot Hunter работает по API-first логике: расписание поставок → проверка окон/коэффициентов → Telegram/email уведомление → бронь после подключения безопасного WB API adapter.",
        "safety": [
            "Не ходим в ЛК WB браузером как человек в основном сценарии.",
            "Ищем только даты из заданного графика, а не случайные окна.",
            "Каждое действие фиксируется в истории.",
            "Автобронь включается только после проверки monitor_only на реальных данных.",
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


async def _notify_no_slots() -> dict[str, Any]:
    text = (
        "WB FBO: окна пока не найдены\n"
        f"Автопоиск продолжает работу по расписанию {_state.get('work_time_from','09:00')}–{_state.get('work_time_to','21:00')}. "
        "Следующая попытка будет выполнена автоматически."
    )
    result = await _send_telegram(text)
    _event("telegram_notification", text, result)
    return result


@router.get("/status")
def status():
    return _status_payload()


@router.post("/config")
def save_config(cfg: BookingConfig):
    data = cfg.model_dump()
    if data.get("interval_workdays") is not None and not data.get("every_n_workdays"):
        data["every_n_workdays"] = data.get("interval_workdays")
    data["coefficient_limit"] = _as_int(data.get("coefficient_limit"), 20)
    data["every_n_workdays"] = _as_int(data.get("every_n_workdays"), 3)
    data["horizon_days"] = _as_int(data.get("horizon_days"), 30)
    data.pop("interval_workdays", None)
    _state.update(data)
    _event("config_saved", "Настройки Slot Hunter сохранены", {k: v for k, v in data.items() if "token" not in k and "chat" not in k})
    return _status_payload()


@router.post("/start")
def start(cfg: BookingConfig | None = None):
    if cfg:
        save_config(cfg)
    _state["enabled"] = True
    _event("started", "Мониторинг Slot Hunter включен")
    return _status_payload()


@router.post("/stop")
def stop():
    _state["enabled"] = False
    _event("stopped", "Мониторинг Slot Hunter остановлен")
    return _status_payload()


@router.post("/check")
async def check_now():
    async with _lock:
        _state["last_check_at"] = _now()
        planned = _workdays_schedule(_state.get("start_date"), _as_int(_state.get("every_n_workdays"), 3), _as_int(_state.get("horizon_days"), 30))
        # В этом безопасном слое не имитируем найденные слоты. Если WB adapter не вернул окно — шлем честный no-slots.
        _state["found_slots"] = []
        notify_result = await _notify_no_slots() if bool(settings.wb_booking_notify_empty_checks) else {"skipped": True}
        _event(
            "check",
            "WB FBO: окна пока не найдены. Автопоиск продолжает работу по расписанию.",
            {"planned_dates": planned[:10], "warehouses": _state.get("warehouses"), "coefficient_limit": _state.get("coefficient_limit"), "notification": notify_result},
        )
        return _status_payload()


@router.post("/notify-test")
async def notify_test():
    result = await _notify_no_slots()
    return {"ok": bool(result.get("ok")), "result": result, "status": _status_payload()}


@router.get("/events")
def events():
    return {"items": _state["events"]}


async def booking_auto_check_loop():
    await asyncio.sleep(10)
    while True:
        try:
            if _state.get("enabled") and _in_work_window():
                await check_now()
        except Exception as exc:
            _state["last_error"] = str(exc)
            _event("error", f"Slot Hunter auto-check error: {exc}")
        await asyncio.sleep(max(60, int(getattr(settings, "wb_booking_auto_check_interval_seconds", 900))))
