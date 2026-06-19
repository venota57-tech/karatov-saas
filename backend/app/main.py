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

try:
    from app.services.autopublish_service import autopublish_once, autopublish_loop
except Exception as e:
    print(f"[startup] autopublish unavailable: {e}")
    autopublish_once = None
    autopublish_loop = None

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
    from app.routes.wb_booking import booking_auto_check_loop
except Exception as e:
    print(f"[startup] Slot Hunter loop unavailable: {e}")
    booking_auto_check_loop = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []

    def start_background_tasks():
        async def run_migrations_safe():
            try:
                await asyncio.wait_for(asyncio.to_thread(run_lightweight_migrations), timeout=20)
                print("[startup] DB migrations completed")
            except Exception as e:
                print(f"[startup] DB migration skipped/error: {e}")

        tasks.append(asyncio.create_task(run_migrations_safe()))

        if wb_auto_sync_loop and (getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")):
            tasks.append(asyncio.create_task(wb_auto_sync_loop()))
            print("[startup] WB auto sync loop scheduled")

        if ozon_auto_sync_loop and settings.ozon_client_id and settings.ozon_api_key:
            tasks.append(asyncio.create_task(ozon_auto_sync_loop()))
            print("[startup] Ozon auto sync loop scheduled")

        if autopublish_loop:
            tasks.append(asyncio.create_task(autopublish_loop()))
            print("[startup] autopublish loop scheduled")

        if booking_auto_check_loop:
            tasks.append(asyncio.create_task(booking_auto_check_loop()))
            print("[startup] Slot Hunter loop scheduled")

    loop = asyncio.get_running_loop()
    loop.call_later(1, start_background_tasks)
    print("[startup] port-first mode: background tasks scheduled after startup")

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


for route in [
    "app.routes.system",
    "app.routes.reviews",
    "app.routes.questions",
    "app.routes.reports",
    "app.routes.summary",
    "app.routes.settings",
    "app.routes.autopublish_settings",
    "app.routes.sync",
    "app.routes.ozon_sync",
    "app.routes.analytics",
    "app.routes.wb_booking",
    "app.routes.ops_history",
    "app.routes.operations",
]:
    include_router_safe(route)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/reviews")
def reviews_compat(
    platform: str | None = None,
    answer_state: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    q = db.query(Review)
    if platform and platform.upper() != "ALL":
        q = q.filter(Review.platform == platform.upper())
    if answer_state == "unanswered":
        q = q.filter(Review.operational_status == "needs_response")
    elif answer_state == "answered":
        q = q.filter(Review.has_answer == True)  # noqa: E712
    rows = q.order_by(Review.created_at_marketplace.desc().nullslast(), Review.id.desc()).limit(min(limit, 2000)).all()
    return jsonable_encoder(rows)


@app.get("/questions")
def questions_compat(
    platform: str | None = None,
    answer_state: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    q = db.query(Question)
    if platform and platform.upper() != "ALL":
        q = q.filter(Question.platform == platform.upper())
    if answer_state == "unanswered":
        q = q.filter(Question.operational_status == "needs_response")
    elif answer_state == "answered":
        q = q.filter(Question.has_answer == True)  # noqa: E712
    rows = q.order_by(Question.created_at_marketplace.desc().nullslast(), Question.id.desc()).limit(min(limit, 2000)).all()
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
    if autopublish_once is None:
        return JSONResponse(status_code=503, content={"error": "autopublish service unavailable"})
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
            "wb_api_key": bool(getattr(settings, "wb_api_token", "")),
            "wb_api_token": bool(getattr(settings, "wb_api_token", "")),
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
    return JSONResponse(status_code=500, content={"error": "frontend/dist/index.html не найден"})


@app.get("/{full_path:path}")
def serve_frontend_fallback(full_path: str):
    api_prefixes = (
        "api/", "system/", "sync/", "settings/", "reports", "summary", "reviews", "questions",
        "autopublish", "wb-booking", "ops/", "operations", "analytics/",
    )
    if full_path.startswith(api_prefixes):
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"error": "Frontend not found"})
