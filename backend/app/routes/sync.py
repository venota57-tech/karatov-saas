from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..services.sync_service import run_sync_wb_with_status, run_sync_wb_block_with_status, run_sync_wb_operational_once, run_sync_wb_backfill_once, get_sync_status, WB_SYNC_BLOCKS

router = APIRouter(prefix='/sync', tags=['sync'])

@router.post('/wb')
async def sync_wildberries_next(db: Session = Depends(get_db)):
    """v2.0: safe sync button runs only the next due block, not all WB endpoints at once."""
    try:
        return await run_sync_wb_with_status(db, source='manual_next_block')
    except Exception as exc:
        raise HTTPException(400, f'Ошибка синхронизации WB: {exc}')

@router.post('/wb/next')
async def sync_wildberries_next_explicit(db: Session = Depends(get_db)):
    try:
        return await run_sync_wb_block_with_status('next', db=db, source='manual_next_block')
    except Exception as exc:
        raise HTTPException(400, f'Ошибка синхронизации WB: {exc}')

@router.post('/wb/block/{block_name}')
async def sync_wildberries_block(block_name: str, db: Session = Depends(get_db)):
    if block_name not in WB_SYNC_BLOCKS:
        raise HTTPException(400, f'Неизвестный блок: {block_name}. Доступно: {", ".join(WB_SYNC_BLOCKS)}')
    try:
        return await run_sync_wb_block_with_status(block_name, db=db, source='manual_block')
    except Exception as exc:
        raise HTTPException(400, f'Ошибка синхронизации блока {block_name}: {exc}')

@router.get('/status')
def sync_status():
    return get_sync_status()

@router.post('/wb/operational/next')
async def sync_wildberries_operational_next(db: Session = Depends(get_db)):
    """Run the next operational queue block: unanswered reviews/questions."""
    try:
        return await run_sync_wb_operational_once(db=db, source='manual_operational')
    except Exception as exc:
        raise HTTPException(400, f'Ошибка операционной синхронизации WB: {exc}')

@router.post('/wb/backfill/next')
async def sync_wildberries_backfill_next(db: Session = Depends(get_db)):
    """Run the next historical backfill block: answered reviews/questions/archive."""
    try:
        return await run_sync_wb_backfill_once(db=db, source='manual_backfill')
    except Exception as exc:
        raise HTTPException(400, f'Ошибка исторической дозагрузки WB: {exc}')
