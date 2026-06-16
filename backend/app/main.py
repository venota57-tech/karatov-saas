from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from contextlib import asynccontextmanager
import asyncio
import os

from app.config import settings
from app.ai.answer_generator import AnswerGenerator
from app.services.autopublish_service import autopublish_once

try:
    from app.services.sync_service import wb_auto_sync_loop, get_sync_status
except Exception as e:
    print(f"[startup] sync service unavailable: {e}")
    wb_auto_sync_loop = None
    get_sync_status = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []

    if wb_auto_sync_loop:
        print("[startup] starting WB auto sync loop")
        tasks.append(asyncio.create_task(wb_auto_sync_loop()))

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
            "openai_api_key": bool(getattr(settings, "openai_api_key", None)),
            "wb_api_token": bool(getattr(settings, "wb_api_token", None)),
            "ozon_client_id": bool(getattr(settings, "ozon_client_id", None)),
            "ozon_api_key": bool(getattr(settings, "ozon_api_key", None)),
        },
        "openai": {
            "model": getattr(settings, "openai_model", None),
        },
        "publishing": {
            "enable_marketplace_publishing": bool(
                getattr(settings, "enable_marketplace_publishing", False)
            )
        },
        "sync": sync,
    }


@app.post("/generate")
async def generate_answer(req: Request):
    data = await req.json()

    try:
        return generator.generate_for_review(data)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/autopublish")
async def autopublish():
    try:
        return await autopublish_once()
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