from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
import os

from app.ai.answer_generator import AnswerGenerator
from app.services.autopublish_service import autopublish_once

try:
    from app.marketplace_clients.wb import WBClient
except Exception:
    WBClient = None


app = FastAPI(title="KARATOV CX Hub")

generator = AnswerGenerator()
wb = WBClient() if WBClient else None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/reviews")
def get_reviews():
    if wb is None:
        return JSONResponse(
            status_code=500,
            content={"error": "WBClient не найден или не инициализировался"}
        )

    try:
        return wb.get_reviews()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.post("/generate")
async def generate_answer(req: Request):
    data = await req.json()

    try:
        return generator.generate_for_review(data)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


@app.post("/autopublish")
async def autopublish():
    try:
        return await autopublish_once()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


# ===== FRONTEND =====

frontend_path = os.path.join(os.path.dirname(__file__), "../frontend/dist")
assets_path = os.path.join(frontend_path, "assets")
index_path = os.path.join(frontend_path, "index.html")

if os.path.exists(assets_path):
    app.mount(
        "/assets",
        StaticFiles(directory=assets_path),
        name="assets"
    )


@app.get("/")
def serve_frontend():
    if os.path.exists(index_path):
        return FileResponse(index_path)

    return JSONResponse(
        status_code=500,
        content={
            "error": "frontend/dist/index.html не найден",
            "frontend_path": frontend_path
        }
    )