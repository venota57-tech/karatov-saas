from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# путь к frontend
FRONTEND_PATH = os.path.join(BASE_DIR, "..", "frontend")
DIST_PATH = os.path.join(FRONTEND_PATH, "dist")

# если фронт не собрался — покажем это явно
@app.get("/")
def root():
    index_file = os.path.join(DIST_PATH, "index.html")

    if os.path.exists(index_file):
        return FileResponse(index_file)

    return {
        "error": "frontend not built",
        "hint": "check docker build logs for npm run build"
    }


# раздача статики если есть
if os.path.exists(DIST_PATH):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(DIST_PATH, "assets")),
        name="assets"
    )


# fallback для react
@app.get("/{full_path:path}")
def spa(full_path: str):
    index_file = os.path.join(DIST_PATH, "index.html")

    if os.path.exists(index_file):
        return FileResponse(index_file)

    return {"error": "frontend not built"}