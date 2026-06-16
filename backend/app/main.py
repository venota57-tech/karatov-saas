from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

@app.get("/reviews")
def get_reviews():
    data = [
        {"text": "Отличный товар"},
        {"text": "Быстрая доставка"},
        {"text": "Плохое качество"}
    ]
    return JSONResponse(content=data, media_type="application/json; charset=utf-8")


# ВАЖНО: правильный путь
app.mount("/", StaticFiles(directory="static", html=True), name="static")