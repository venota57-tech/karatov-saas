from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta

router = APIRouter(prefix="/wb-booking", tags=["wb-booking"])

_state = {
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
    "telegram_enabled": False,
    "telegram_chat_ids": [],
    "email_enabled": False,
    "email_recipients": [],
    "last_check_at": None,
    "last_error": None,
    "events": [],
    "found_slots": [],
}


class BookingConfig(BaseModel):
    enabled: bool = False
    mode: str = Field("monitor_only", description="monitor_only | notify_only | auto_book")
    warehouses: list[str] = ["Коледино", "Электросталь"]
    supply_type: str = "Суперсейф"
    coefficient_limit: int = 20
    start_date: str | None = None
    every_n_workdays: int = 3
    horizon_days: int = 30
    work_time_from: str = "09:00"
    work_time_to: str = "21:00"
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_ids: list[str] = []
    email_enabled: bool = False
    email_recipients: list[str] = []


def _now():
    return datetime.now(timezone.utc).isoformat()


def _event(kind: str, message: str, payload: dict | None = None):
    row = {"at": _now(), "kind": kind, "message": message, "payload": payload or {}}
    _state["events"].insert(0, row)
    _state["events"] = _state["events"][:200]
    return row


def _workdays_schedule(start_date: str | None, every: int, horizon: int):
    if not start_date:
        return []
    try:
        current = datetime.fromisoformat(start_date).date()
    except Exception:
        return []
    end = current + timedelta(days=max(1, horizon))
    result = []
    step_count = 0
    while current <= end:
        if current.weekday() < 5:
            if step_count % max(1, every) == 0:
                result.append(current.isoformat())
            step_count += 1
        current += timedelta(days=1)
    return result[:50]


@router.get("/status")
def status():
    return {
        **_state,
        "planned_dates": _workdays_schedule(_state.get("start_date"), int(_state.get("every_n_workdays") or 3), int(_state.get("horizon_days") or 30)),
        "api_first": True,
        "browser_automation": False,
        "description": "Slot Hunter работает по API-first логике: проверка окон/коэффициентов, сверка с расписанием, уведомление Telegram/email, затем ручная или автоматическая бронь при подключении метода бронирования.",
    }


@router.post("/config")
def save_config(cfg: BookingConfig):
    data = cfg.model_dump()
    # Токен не возвращаем в UI и не храним в открытом виде в этом MVP-state.
    data.pop("telegram_bot_token", None)
    _state.update(data)
    _event("config_saved", "Настройки Slot Hunter сохранены", {k: v for k, v in data.items() if "token" not in k})
    return status()


@router.post("/start")
def start(cfg: BookingConfig | None = None):
    if cfg:
        save_config(cfg)
    _state["enabled"] = True
    _event("started", "Мониторинг Slot Hunter включен")
    return status()


@router.post("/stop")
def stop():
    _state["enabled"] = False
    _event("stopped", "Мониторинг Slot Hunter остановлен")
    return status()


@router.post("/check")
def check_now():
    _state["last_check_at"] = _now()
    planned = _workdays_schedule(_state.get("start_date"), int(_state.get("every_n_workdays") or 3), int(_state.get("horizon_days") or 30))
    # В этом слое нет реального вызова WB booking API: он подключается отдельным методом после подтверждения endpoint/прав доступа.
    _event("check", "Проверка расписания выполнена. Реальный API-поиск слотов будет подключен к WB endpoint отдельным безопасным слоем.", {"planned_dates": planned[:10]})
    return status()


@router.get("/events")
def events():
    return {"items": _state["events"]}
