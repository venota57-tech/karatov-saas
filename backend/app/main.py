from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
import os

app = FastAPI()

# абсолютный путь к папке static
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")


# главная страница (UI)
@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# API с правильной кодировкой
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