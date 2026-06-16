from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..schemas import AutomationRulesPayload, AutomationRulesOut
from ..services.automation_rules import get_rules, update_rules
from ..config import settings

router = APIRouter(prefix='/settings', tags=['settings'])


@router.get('/openai-status')
def openai_status(db: Session = Depends(get_db)):
    rules = get_rules(db).rules or {}
    return {
        'api_key_found': bool(settings.openai_api_key),
        'model': settings.openai_model,
        'ai_generation_enabled': bool(rules.get('ai_generation_enabled', True)),
        'fallback_to_local_templates': bool(rules.get('ai_fallback_to_local_templates', True)),
        'publishing_enabled': bool(settings.enable_marketplace_publishing),
        'mode': 'real_publish' if settings.enable_marketplace_publishing else 'dry_run',
    }

@router.get('/automation-rules', response_model=AutomationRulesOut)
def read_automation_rules(db: Session = Depends(get_db)):
    row = get_rules(db)
    return {**(row.rules or {}), 'updated_at': row.updated_at}

@router.put('/automation-rules', response_model=AutomationRulesOut)
def save_automation_rules(payload: AutomationRulesPayload, db: Session = Depends(get_db)):
    row = update_rules(db, payload.model_dump())
    return {**(row.rules or {}), 'updated_at': row.updated_at}
