from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter

router = APIRouter(prefix="/wb-booking", tags=["wb-booking"])

_STATE = {
    "enabled": False,
    "mode": "ui_or_api_ready",
    "warehouses": ["Коледино", "Электросталь"],
    "supply_type": "Суперсейф",
    "coefficient_limit": 20,
    "working_window": "09:00-21:00",
    "last_started_at": None,
    "last_stopped_at": None,
    "last_event": "Модуль UI восстановлен. Для реального бронирования нужен подтвержденный способ доступа WB: API endpoint бронирования или Playwright-сессия ЛК продавца.",
}


@router.get("/status")
def status():
    return _STATE


@router.post("/start")
def start(payload: dict | None = None):
    _STATE["enabled"] = True
    _STATE["last_started_at"] = datetime.utcnow().isoformat()
    if payload:
        _STATE.update({k: v for k, v in payload.items() if k in {"warehouses", "coefficient_limit", "working_window", "mode"}})
    _STATE["last_event"] = "Мониторинг слотов включен в интерфейсе. Реальное создание поставки будет подключено после подтверждения WB-доступа."
    return _STATE


@router.post("/stop")
def stop():
    _STATE["enabled"] = False
    _STATE["last_stopped_at"] = datetime.utcnow().isoformat()
    _STATE["last_event"] = "Мониторинг слотов остановлен."
    return _STATE
