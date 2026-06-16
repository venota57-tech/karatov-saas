from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import asyncio
import os

from app.database import get_db, run_lightweight_migrations
from app.config import settings
from app.models import Review, Question
from app.ai.answer_generator import AnswerGenerator
from app.services.autopublish_service import autopublish_once

try:
    from app.services.sync_service import wb_auto_sync_loop, get_sync_status
except Exception as e:
    print(f"[startup] sync service unavailable: {e}")
    wb_auto_sync_loop = None
    get_sync_status = None

try:
    from app.services.ozon_sync_service import ozon_auto_sync_loop
except Exception as e:
    print(f"[startup] ozon sync service unavailable: {e}")
    ozon_auto_sync_loop = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []

    try:
        print("[startup] running lightweight migrations")
        run_lightweight_migrations()
    except Exception as e:
        print(f"[startup] migration error: {e}")

    if wb_auto_sync_loop and settings.wb_api_token:
        print("[startup] starting WB auto sync loop")
        tasks.append(asyncio.create_task(wb_auto_sync_loop()))

    if ozon_auto_sync_loop and settings.ozon_client_id and settings.ozon_api_key:
        print("[startup] starting OZON auto sync loop")
        tasks.append(asyncio.create_task(ozon_auto_sync_loop()))

    yield

    for task in tasks:
        task.cancel()


app = FastAPI(title="KARATOV CX Hub", lifespan=lifespan)
generator = AnswerGenerator()


def include_router_safe(module_path: str, router_name: str = "router"):
    try:
        module = __import__(module_path, fromlist=[router_name])
        router = getattr(module, router_name)
        app.include_router(router)
        print(f"[router] connected: {module_path}")
    except Exception as e:
        print(f"[router] skipped {module_path}: {e}")


include_router_safe("app.routes.system")
include_router_safe("app.routes.reviews")
include_router_safe("app.routes.questions")
include_router_safe("app.routes.reports")
include_router_safe("app.routes.summary")
include_router_safe("app.routes.settings")
include_router_safe("app.routes.autopublish_settings")
include_router_safe("app.routes.sync")
include_router_safe("app.routes.analytics")
include_router_safe("app.routes.ozon_sync")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/system/status")
def system_status():
    sync = None
    if get_sync_status:
        try:
            sync = get_sync_status()
        except Exception as e:
            sync = {"error": str(e)}

    return {
        "status": "ok",
        "keys": {
            "openai_api_key": bool(settings.openai_api_key),
            "wb_api_key": bool(settings.wb_api_token),
            "wb_api_token": bool(settings.wb_api_token),
            "ozon_client_id": bool(settings.ozon_client_id),
            "ozon_api_key": bool(settings.ozon_api_key),
        },
        "openai": {
            "model": settings.openai_model,
        },
        "publishing": {
            "enable_marketplace_publishing": bool(settings.enable_marketplace_publishing),
        },
        "sync": sync,
    }


@app.get("/reviews")
def reviews_compat(db: Session = Depends(get_db)):
    rows = (
        db.query(Review)
        .order_by(Review.created_at_marketplace.desc().nullslast(), Review.id.desc())
        .limit(500)
        .all()
    )
    return jsonable_encoder(rows)


@app.get("/questions")
def questions_compat(db: Session = Depends(get_db)):
    rows = (
        db.query(Question)
        .order_by(Question.created_at_marketplace.desc().nullslast(), Question.id.desc())
        .limit(500)
        .all()
    )
    return jsonable_encoder(rows)


@app.post("/generate")
async def generate_answer(req: Request):
    data = await req.json()

    try:
        if "rating" in data:
            return generator.generate_for_review_until_pass(data)
        return generator.generate_for_question_until_pass(data)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/autopublish")
async def autopublish():
    try:
        return await autopublish_once()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# совместимость с текущим UI
@app.post("/ozon-sync/all")
async def ozon_sync_all_compat():
    try:
        from app.services.ozon_sync_service import run_ozon_sync_all_with_status
        return await run_ozon_sync_all_with_status(source="manual_ui")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


frontend_path = os.path.join(os.path.dirname(__file__), "../frontend/dist")
assets_path = os.path.join(frontend_path, "assets")
index_path = os.path.join(frontend_path, "index.html")

if os.path.exists(assets_path):
    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")


@app.get("/")
def serve_frontend():
    if os.path.exists(index_path):
        return FileResponse(index_path)

    return JSONResponse(
        status_code=500,
        content={
            "error": "frontend/dist/index.html не найден",
            "frontend_path": frontend_path,
        },
    )