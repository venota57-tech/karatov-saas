from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# отдаём собранный фронт
app.mount("/", StaticFiles(directory="backend/static", html=True), name="static")

# API
@app.get("/reviews")
def reviews():
    return [
        {"text": "Отличный товар"},
        {"text": "Быстрая доставка"},
        {"text": "Плохое качество"}
    ]