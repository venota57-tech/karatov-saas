from __future__ import annotations

from fastapi import APIRouter

from ..services.wb_booking_service import get_status, save_config, check_slots_once

router = APIRouter(prefix="/wb-booking", tags=["wb-booking"])


@router.get("/status")
def status():
    return get_status()


@router.post("/config")
def config(payload: dict):
    return {"ok": True, "config": save_config(payload)}


@router.post("/start")
def start(payload: dict | None = None):
    cfg = save_config({**(payload or {}), "enabled": True})
    return {"ok": True, "message": "WB Slot Hunter включен", "config": cfg, "status": get_status()}


@router.post("/stop")
def stop():
    cfg = save_config({"enabled": False})
    return {"ok": True, "message": "WB Slot Hunter остановлен", "config": cfg, "status": get_status()}


@router.post("/check")
async def check():
    return await check_slots_once(source="manual")
