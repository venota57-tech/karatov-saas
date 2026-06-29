from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import SessionLocal, get_db
from app.services.recovery_v5 import RecoveryV5
from app.services.recovery_v5_stages import run_stage
router=APIRouter(prefix='/recovery-v5', tags=['recovery-v5'])
STATE={'running':False,'last_error':None,'last_result':None}; TASK=None
def iso(): return datetime.now(timezone.utc).isoformat()
async def bg(platform, deep, rid):
    db=SessionLocal()
    try:
        res=await RecoveryV5(db).all(platform, deep); STATE.update({'running':False,'run_id':rid,'last_error':None,'last_result':res,'last_finished_at':iso()})
    except Exception as e:
        try: db.rollback()
        except Exception: pass
        STATE.update({'running':False,'run_id':rid,'last_error':str(e),'last_finished_at':iso()})
    finally: db.close()
@router.get('/status')
def status(): return {'ok':True,'state':STATE}
@router.post('/sync/start')
async def start(platform:str='ALL', deep:bool=False):
    global TASK
    if TASK is not None and not TASK.done(): return {'ok':True,'started':False,'already_running':True,'state':STATE}
    rid=str(uuid4()); STATE.update({'running':True,'run_id':rid,'platform':platform.upper(),'deep':deep,'last_started_at':iso(),'last_result':None,'last_error':None}); TASK=asyncio.create_task(bg(platform.upper(),deep,rid)); return {'ok':True,'started':True,'run_id':rid}
@router.post('/sync/run-now')
async def run_now(platform:str='ALL', deep:bool=False, db:Session=Depends(get_db)): return await RecoveryV5(db).all(platform.upper(), deep)
@router.get('/diagnostics')
def diagnostics(platform:str='ALL', limit:int=200, db:Session=Depends(get_db)): return RecoveryV5(db).diagnostics(platform.upper(), limit)
@router.get('/sla')
def sla(platform:str='ALL', days:int=30, db:Session=Depends(get_db)): return RecoveryV5(db).sla(platform.upper(), days)
@router.get('/chats/{chat_id}/messages')
def chat_messages(chat_id:int, limit:int=500, db:Session=Depends(get_db)): return RecoveryV5(db).chat_messages(chat_id, limit)

@router.get('/communications')
def communications(platform:str='ALL', limit:int=20000, entity_type:str='ALL', topic:str|None=None, db:Session=Depends(get_db)):
    return RecoveryV5(db).communications(platform.upper(), limit, entity_type, topic)

@router.get('/operations')
def operations_items(platform:str='ALL', limit:int=10000, operation_type:str='ALL', db:Session=Depends(get_db)):
    return RecoveryV5(db).operations_items(platform.upper(), limit, operation_type)

@router.get('/topics')
def topics(platform:str='ALL', db:Session=Depends(get_db)):
    return RecoveryV5(db).topics(platform.upper())

@router.get('/topics/{topic}/items')
def topic_items(topic:str, platform:str='ALL', limit:int=20000, db:Session=Depends(get_db)):
    return RecoveryV5(db).communications(platform.upper(), limit, 'ALL', topic)

@router.get('/scheduler')
def scheduler(db:Session=Depends(get_db)):
    return RecoveryV5(db).scheduler()
@router.get('/counts')
def counts(db:Session=Depends(get_db)):
    return RecoveryV5(db).counts()


STAGE_STATE={'running':False,'last_error':None,'last_result':None,'last_started_at':None,'last_finished_at':None}
STAGE_TASK=None

async def bg_stage(stage, platform, deep, rid):
    db=SessionLocal()
    try:
        res=await run_stage(RecoveryV5(db), stage=stage, platform=platform, deep=deep)
        STAGE_STATE.update({'running':False,'run_id':rid,'stage':stage,'platform':platform,'deep':deep,'last_error':None,'last_result':res,'last_finished_at':iso()})
    except Exception as e:
        try: db.rollback()
        except Exception: pass
        STAGE_STATE.update({'running':False,'run_id':rid,'stage':stage,'platform':platform,'deep':deep,'last_error':str(e),'last_finished_at':iso()})
    finally:
        db.close()

@router.get('/stage/status')
def stage_status():
    return {'ok':True,'state':STAGE_STATE}

@router.post('/stage/start')
async def stage_start(stage:str='all_safe', platform:str='ALL', deep:bool=False):
    global STAGE_TASK
    if STAGE_TASK is not None and not STAGE_TASK.done():
        return {'ok':True,'started':False,'already_running':True,'state':STAGE_STATE}
    rid=str(uuid4())
    STAGE_STATE.update({'running':True,'run_id':rid,'stage':stage,'platform':platform.upper(),'deep':deep,'last_started_at':iso(),'last_finished_at':None,'last_error':None,'last_result':None})
    STAGE_TASK=asyncio.create_task(bg_stage(stage,platform.upper(),deep,rid))
    return {'ok':True,'started':True,'run_id':rid,'stage':stage,'platform':platform.upper(),'deep':deep}

@router.post('/stage/run-now')
async def stage_run_now(stage:str='all_safe', platform:str='ALL', deep:bool=False, db:Session=Depends(get_db)):
    return await run_stage(RecoveryV5(db), stage=stage, platform=platform.upper(), deep=deep)

