from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

@app.get("/")
def root():
    return FileResponse(
        os.path.join(FRONTEND_DIR, "index.html"),
        media_type="text/html"
    )

# чтобы js грузился
app.mount("/src", StaticFiles(directory=os.path.join(FRONTEND_DIR, "src")), name="src")

@app.get("/reviews")
def get_reviews():
    return [
        {"text": "Отличный товар"},
        {"text": "Быстрая доставка"},
        {"text": "Не понравилось качество"}
    ]