from __future__ import annotations

from fastapi import APIRouter, Body

from ..services.wb_booking_service import (
    get_booking_status,
    update_booking_config,
    check_slots_once,
    start_booking,
    stop_booking,
)

router = APIRouter(prefix="/wb-booking", tags=["wb-booking"])


@router.get("/status")
def status():
    return get_booking_status()


@router.get("/config")
def config():
    return get_booking_status()


@router.post("/config")
def save_config(payload: dict = Body(default_factory=dict)):
    return update_booking_config(payload)


@router.post("/check")
async def check():
    return await check_slots_once()


@router.post("/start")
def start(payload: dict = Body(default_factory=dict)):
    return start_booking(payload)


@router.post("/stop")
def stop():
    return stop_booking()
