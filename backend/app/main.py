from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

static_path = os.path.join(BASE_DIR, "../static")

app.mount("/", StaticFiles(directory=static_path, html=True), name="static")


@app.get("/reviews")
def reviews():
    return [
        {"text": "Отличный товар"},
        {"text": "Быстрая доставка"},
        {"text": "Плохое качество"}
    ]