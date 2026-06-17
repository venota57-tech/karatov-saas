from __future__ import annotations

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
from pydantic import BaseModel, Field

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
    "telegram_enabled": True,
    "telegram_connected": True,
    "telegram_status": "Подключен через существующую группу/бота. Chat ID в интерфейсе не требуется.",
    "email_enabled": False,
    "email_recipients": [],
    "last_check_at": None,
    "last_error": None,
    "events": [],
    "found_slots": [],
    "booking_history": [],
}


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


def _now():
    return datetime.now(timezone.utc).isoformat()


def _event(kind: str, message: str, payload: dict | None = None):
    row = {"at": _now(), "kind": kind, "message": message, "payload": payload or {}}
    _state["events"].insert(0, row)
    _state["events"] = _state["events"][:200]
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


def _status_payload():
    every = _as_int(_state.get("every_n_workdays"), 3)
    horizon = _as_int(_state.get("horizon_days"), 30)
    planned = _workdays_schedule(_state.get("start_date"), every, horizon)
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
        "description": "Slot Hunter работает по API-first логике: проверка окон/коэффициентов, сверка с расписанием, уведомление Telegram/email, затем ручная или автоматическая бронь при подключении метода бронирования.",
        "safety": [
            "Не ходим в ЛК WB браузером как человек в основном сценарии.",
            "Ищем только даты из заданного графика, а не случайные окна.",
            "Каждое действие фиксируется в истории.",
            "Автобронь включается только после проверки monitor_only на реальных данных.",
        ],
    }


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
def check_now():
    _state["last_check_at"] = _now()
    planned = _workdays_schedule(_state.get("start_date"), _as_int(_state.get("every_n_workdays"), 3), _as_int(_state.get("horizon_days"), 30))
    _event(
        "check",
        "Расписание проверено. Реальный API-адаптер WB для поиска/бронирования подключается отдельным безопасным слоем.",
        {"planned_dates": planned[:10], "warehouses": _state.get("warehouses"), "coefficient_limit": _state.get("coefficient_limit")},
    )
    return _status_payload()


@router.get("/events")
def events():
    return {"items": _state["events"]}
