from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..services.ozon_sync_service import sync_ozon_all, sync_ozon_block, get_ozon_status

router = APIRouter(prefix='/sync/ozon', tags=['sync-ozon'])

@router.post('')
async def sync_ozon(db: Session = Depends(get_db)):
    try:
        return await sync_ozon_all(db)
    except Exception as exc:
        raise HTTPException(400, f'Ошибка синхронизации Ozon: {exc}')

@router.post('/block/{block_name}')
async def sync_ozon_single_block(block_name: str, db: Session = Depends(get_db)):
    if block_name not in {'reviews_unanswered','reviews_answered','questions_unanswered','questions_answered'}:
        raise HTTPException(400, 'Неизвестный блок Ozon')
    try:
        return await sync_ozon_block(db, block_name)
    except Exception as exc:
        raise HTTPException(400, f'Ошибка синхронизации Ozon {block_name}: {exc}')

@router.get('/status')
def status():
    return get_ozon_status()
