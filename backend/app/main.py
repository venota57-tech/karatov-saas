from contextlib import asynccontextmanager
import asyncio
import os

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, run_lightweight_migrations, SessionLocal
from app.models import Review, Question
from app.ai.answer_generator import AnswerGenerator
from app.services.autopublish_service import autopublish_once, autopublish_loop

try:
    from app.services.sync_service import wb_auto_sync_loop, get_sync_status
except Exception as e:
    print(f"[startup] WB sync unavailable: {e}")
    wb_auto_sync_loop = None
    get_sync_status = None

try:
    from app.services.ozon_sync_service import ozon_auto_sync_loop, sync_ozon_all
except Exception as e:
    print(f"[startup] Ozon sync unavailable: {e}")
    ozon_auto_sync_loop = None
    sync_ozon_all = None

try:
    from app.services.wb_booking_service import wb_booking_loop, ensure_booking_tables
except Exception as e:
    print(f"[startup] WB booking unavailable: {e}")
    wb_booking_loop = None
    ensure_booking_tables = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []

    try:
        run_lightweight_migrations()
        print("[startup] DB migrations completed")
    except Exception as e:
        print(f"[startup] DB migration error: {e}")

    try:
        if ensure_booking_tables:
            ensure_booking_tables()
            print("[startup] booking tables ready")
    except Exception as e:
        print(f"[startup] booking migration error: {e}")

    if wb_auto_sync_loop and settings.wb_api_token:
        tasks.append(asyncio.create_task(wb_auto_sync_loop()))
        print("[startup] WB auto sync loop started")

    if ozon_auto_sync_loop and settings.ozon_client_id and settings.ozon_api_key:
        tasks.append(asyncio.create_task(ozon_auto_sync_loop()))
        print("[startup] Ozon auto sync loop started")

    if wb_booking_loop and settings.wb_api_token:
        tasks.append(asyncio.create_task(wb_booking_loop()))
        print("[startup] WB booking loop started")

    tasks.append(asyncio.create_task(autopublish_loop()))
    print("[startup] autopublish loop started")

    yield

    for task in tasks:
        task.cancel()


app = FastAPI(title="KARATOV CX Hub", lifespan=lifespan)
generator = AnswerGenerator()


def include_router_safe(module_path: str):
    try:
        module = __import__(module_path, fromlist=["router"])
        app.include_router(module.router)
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
include_router_safe("app.routes.ozon_sync")
include_router_safe("app.routes.analytics")
include_router_safe("app.routes.wb_booking")


@app.get("/health")
def health():
    return {"status": "ok"}


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


@app.post("/ozon-sync/all")
async def ozon_sync_all_compat():
    if sync_ozon_all is None:
        return JSONResponse(status_code=500, content={"error": "Ozon sync service недоступен"})

    db = SessionLocal()
    try:
        return await sync_ozon_all(db)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        db.close()


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
        "openai": {"model": settings.openai_model},
        "publishing": {
            "enable_marketplace_publishing": bool(settings.enable_marketplace_publishing),
            "mode": "real_publish" if settings.enable_marketplace_publishing else "dry_run",
        },
        "sync": sync,
    }


frontend_path = os.path.join(os.path.dirname(__file__), "../frontend/dist")
assets_path = os.path.join(frontend_path, "assets")
index_path = os.path.join(frontend_path, "index.html")

if os.path.exists(assets_path):
    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")


@app.get("/")
def serve_frontend():
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=500, content={"error": "frontend/dist/index.html не найден", "frontend_path": frontend_path})


@app.get("/{full_path:path}")
def serve_frontend_fallback(full_path: str):
    if full_path.startswith(("api/", "system/", "sync/", "settings/", "reports", "summary", "reviews", "questions", "wb-booking")):
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"error": "Frontend not found"})
