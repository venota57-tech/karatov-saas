from fastapi import APIRouter, Request
from typing import List, Dict, Any

# твой AI пайплайн
from app.ai.answer_generator import generate_answer
from app.ai.quality_gate import check_quality

router = APIRouter()

# ======================
# ПОЛУЧЕНИЕ ОТЗЫВОВ
# ======================

@router.get("/reviews")
async def get_reviews():
    # временно (или у тебя уже есть реальный источник)
    return [
        {"text": "Отличный товар"},
        {"text": "Быстрая доставка"},
        {"text": "Плохое качество"}
    ]


# ======================
# ГЕНЕРАЦИЯ ОТВЕТА
# ======================

@router.post("/reviews/generate")
async def generate_review_answer(req: Request):
    data = await req.json()
    text = data.get("text", "")

    try:
        # 1. генерация (твоя логика)
        raw_answer = await generate_answer(text)

        # 2. quality gate (если есть)
        final_answer = check_quality(raw_answer)

    except Exception as e:
        final_answer = f"Ошибка генерации: {str(e)}"

    return {
        "answer": final_answer
    }