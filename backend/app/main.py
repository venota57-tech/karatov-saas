from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app = FastAPI()

# путь к frontend
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

# статика (css/js если появятся)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(
        path=FRONTEND_DIR / "index.html",
        media_type="text/html"
    )


# тестовый endpoint (оставь если есть)
@app.get("/health")
def health():
    return {"status": "ok"}