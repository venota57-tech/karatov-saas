from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse

app = FastAPI()


@app.get("/reviews")
def get_reviews():
    data = [
        {"text": "Отличный товар"},
        {"text": "Быстрая доставка"},
        {"text": "Плохое качество"}
    ]
    return JSONResponse(content=data, media_type="application/json; charset=utf-8")


@app.get("/")
def root():
    return FileResponse("backend/static/index.html")