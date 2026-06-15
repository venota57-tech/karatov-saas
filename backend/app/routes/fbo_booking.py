from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import fbo_booking_service as svc

router = APIRouter(prefix='/fbo-booking', tags=['fbo-booking'])

class SettingsPayload(BaseModel):
    enabled: bool | None = None
    warehouses: list[str] | None = None
    supply_type: str | None = None
    max_coefficient: int | None = None
    start_date: str | None = None
    step_working_days: int | None = None
    planning_horizon_days: int | None = None
    search_from: str | None = None
    search_to: str | None = None
    check_interval_seconds: int | None = None
    not_found_notify_after_minutes: int | None = None
    auto_book: bool | None = None
    draft_mode: str | None = None
    telegram_enabled: bool | None = None
    email_enabled: bool | None = None
    interface_notifications_enabled: bool | None = None
    holidays_mode: str | None = None
    excluded_dates: list[str] | None = None
    transport_holidays: list[str] | None = None
    search_source_mode: str | None = None
    wb_final_booking_source: str | None = None
    wb_api_monitor_enabled: bool | None = None
    wb_cabinet_enabled: bool | None = None
    wb_cabinet_profile_alias: str | None = None
    wb_cabinet_url: str | None = None
    wb_cabinet_safe_mode: bool | None = None
    scheduler_enabled: bool | None = None
    wb_api_429_backoff_seconds: int | None = None
    wb_api_request_min_delay_seconds: int | None = None
    wb_cabinet_check_interval_seconds: int | None = None
    wb_cabinet_error_backoff_seconds: int | None = None
    wb_cabinet_real_booking_enabled: bool | None = None
    wb_cabinet_booking_mode: str | None = None
    wb_cabinet_template_quantity: int | None = None
    wb_cabinet_default_warehouse: str | None = None
    wb_cabinet_live_view_enabled: bool | None = None
    wb_cabinet_route_step_mode: str | None = None
    wb_cabinet_require_final_confirmation: bool | None = None
    wb_cabinet_final_confirmation_text: str | None = None
    wb_cabinet_expected_company: str | None = None
    wb_cabinet_detected_company: str | None = None

class ExcludedDatePayload(BaseModel):
    date: str
    reason: str = ''

class ManualTargetPayload(BaseModel):
    date: str
    comment: str = ''

class EditTargetDatePayload(BaseModel):
    new_date: str
    comment: str = ''

class WbCabinetPayload(BaseModel):
    profile_alias: str = 'default'

@router.get('/state')
async def get_state():
    state = await svc.read_state()
    state['runtime'] = svc.runtime_status()
    return state

@router.put('/settings')
async def update_settings(payload: SettingsPayload):
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    return await svc.update_settings(data)

@router.post('/run-now')
async def run_now():
    return await svc.run_check_once(manual=True)

@router.post('/excluded-dates')
async def add_excluded_date(payload: ExcludedDatePayload):
    return await svc.add_excluded_date(payload.date, payload.reason)

@router.post('/targets')
async def add_manual_target(payload: ManualTargetPayload):
    try:
        return await svc.add_manual_target(payload.date, payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.patch('/targets/{target_date}')
async def edit_target_date(target_date: str, payload: EditTargetDatePayload):
    try:
        return await svc.edit_target_date(target_date, payload.new_date, payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

@router.post('/wb-api/audit')
async def wb_api_audit():
    return await svc.wb_api_audit()

@router.post('/wb-api/monitor')
async def wb_api_monitor():
    return await svc.wb_api_monitor_targets()

@router.get('/wb-cabinet/status')
async def wb_cabinet_status():
    return await svc.wb_cabinet_status()

@router.post('/wb-cabinet/start-login')
async def wb_cabinet_start_login(payload: WbCabinetPayload):
    return await svc.wb_cabinet_start_login(payload.profile_alias)

@router.post('/wb-cabinet/check-login')
async def wb_cabinet_check_login(payload: WbCabinetPayload):
    return await svc.wb_cabinet_check_login(payload.profile_alias)

@router.post('/wb-cabinet/monitor')
async def wb_cabinet_monitor(payload: WbCabinetPayload):
    return await svc.wb_cabinet_monitor_targets(payload.profile_alias, manual=True)

@router.get('/wb-cabinet/live-status')
async def wb_cabinet_live_status():
    return await svc.wb_cabinet_live_status()

@router.post('/wb-cabinet/stop-route')
async def wb_cabinet_stop_route():
    return await svc.wb_cabinet_stop_route()

@router.post('/wb-cabinet/book-next')
async def wb_cabinet_book_next(payload: WbCabinetPayload):
    return await svc.wb_cabinet_book_next_safe(payload.profile_alias)

@router.post('/full-wb-cycle')
async def full_wb_cycle():
    return await svc.full_wb_first_tick()

# POSTAVLENO automation endpoints are intentionally disabled in WB-first build.
# Manual POSTAVLENO signal can still be used outside the app, but the hub no longer opens Telegram Web or reads personal chats.
@router.post('/postavleno-account')
@router.post('/postavleno-session/request-code')
@router.post('/postavleno-session/confirm-code')
@router.post('/postavleno-session/check-messages')
@router.post('/postavleno-session/sync-requests')
@router.get('/postavleno-web/status')
@router.post('/postavleno-web/start-login')
@router.post('/postavleno-web/check-login')
@router.post('/postavleno-web/check-messages')
@router.post('/postavleno-web/create-requests')
@router.post('/postavleno-web/submit-2fa')
@router.post('/full-hybrid-tick')
async def postavleno_disabled():
    return await svc.postavleno_disabled_state()

@router.post('/test-telegram')
async def test_telegram():
    return await svc.send_telegram_test()

@router.post('/test-email')
async def test_email():
    try:
        return svc.send_email_test_sync()
    except Exception as exc:
        return {'ok': False, 'message': str(exc)}
