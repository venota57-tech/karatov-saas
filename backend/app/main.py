from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")


# API
@app.get("/reviews")
def reviews():
    return JSONResponse(
        content=[
            {"text": "Отличный товар"},
            {"text": "Быстрая доставка"},
            {"text": "Плохое качество"}
        ],
        media_type="application/json; charset=utf-8"
    )


# ВАЖНО: ОТДАЕМ ВЕСЬ ФРОНТ
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")