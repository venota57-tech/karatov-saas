from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")

# статика (css/js)
app.mount("/src", StaticFiles(directory=os.path.join(FRONTEND_DIR, "src")), name="src")

# ГЛАВНАЯ СТРАНИЦА
@app.get("/")
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))