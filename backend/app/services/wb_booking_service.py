from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

_booking_state: dict[str, Any] = {
    "enabled": False,
    "mode": "monitor_only",
    "warehouses": ["Коледино", "Электросталь"],
    "supply_type": "Суперсейф",
    "coefficient_limit": 20,
    "start_date": None,
    "interval_workdays": 3,
    "horizon_days": 30,
    "work_time_from": "09:00",
    "work_time_to": "21:00",
    "recipients": [],
    "last_check_at": None,
    "last_found_slots": [],
    "history": [],
    "booking_history": [],
    "api_mode": "api_first",
    "description": "API-first Slot Hunter: проверяет подходящие окна по расписанию поставок, складам, типу поставки и коэффициенту. Browser automation не используется как основной механизм.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(value: str | None) -> date:
    if value:
        try:
            return date.fromisoformat(value[:10])
        except Exception:
            pass
    return date.today()


def _is_workday(d: date) -> bool:
    return d.weekday() < 5


def _add_workdays(start: date, days: int) -> date:
    cur = start
    left = days
    while left > 0:
        cur += timedelta(days=1)
        if _is_workday(cur):
            left -= 1
    return cur


def build_target_dates(config: dict[str, Any] | None = None) -> list[str]:
    cfg = {**_booking_state, **(config or {})}
    start = _parse_date(cfg.get("start_date"))
    interval = max(1, int(cfg.get("interval_workdays") or 3))
    horizon = max(1, int(cfg.get("horizon_days") or 30))
    result: list[str] = []
    cur = start
    end = start + timedelta(days=horizon)
    while cur <= end:
        if _is_workday(cur):
            result.append(cur.isoformat())
            cur = _add_workdays(cur, interval)
        else:
            cur += timedelta(days=1)
    return result[:60]


def get_booking_status() -> dict[str, Any]:
    status = dict(_booking_state)
    status["target_dates"] = build_target_dates(status)
    status["reliability_notes"] = [
        "Основной режим — WB API, без входа в личный кабинет через браузер.",
        "Сервис ищет только даты из заданного графика, а не случайные окна.",
        "Каждое найденное окно и каждая попытка бронирования фиксируются в журнале.",
        "Автобронь стоит включать только после проверки monitor_only на реальных окнах.",
    ]
    return status


def update_booking_config(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "enabled", "mode", "warehouses", "supply_type", "coefficient_limit", "start_date",
        "interval_workdays", "horizon_days", "work_time_from", "work_time_to", "recipients",
    }
    for key, value in (payload or {}).items():
        if key in allowed:
            _booking_state[key] = value
    _booking_state["history"].insert(0, {
        "at": _now(),
        "event": "config_updated",
        "payload": {k: _booking_state.get(k) for k in allowed},
    })
    _booking_state["history"] = _booking_state["history"][:200]
    return get_booking_status()


async def check_slots_once() -> dict[str, Any]:
    """
    Production-safe placeholder: does not fake successful booking.
    Real WB slot reading/booking should be wired to official WB FBW/tariffs API methods.
    """
    target_dates = build_target_dates(_booking_state)
    checked_at = _now()
    _booking_state["last_check_at"] = checked_at

    event = {
        "at": checked_at,
        "event": "check_completed",
        "mode": _booking_state.get("mode"),
        "warehouses": _booking_state.get("warehouses"),
        "target_dates_count": len(target_dates),
        "target_dates_preview": target_dates[:10],
        "found": 0,
        "status": "api_adapter_pending",
        "message": "Расписание построено. Для реального поиска нужно подключить конкретный WB API-метод коэффициентов/слотов к адаптеру.",
    }
    _booking_state["history"].insert(0, event)
    _booking_state["history"] = _booking_state["history"][:200]
    _booking_state["last_found_slots"] = []
    return get_booking_status()


def start_booking(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if payload:
        update_booking_config(payload)
    _booking_state["enabled"] = True
    _booking_state["history"].insert(0, {"at": _now(), "event": "slot_hunter_started", "mode": _booking_state.get("mode")})
    return get_booking_status()


def stop_booking() -> dict[str, Any]:
    _booking_state["enabled"] = False
    _booking_state["history"].insert(0, {"at": _now(), "event": "slot_hunter_stopped"})
    return get_booking_status()
