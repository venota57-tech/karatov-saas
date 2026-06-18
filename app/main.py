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
    from app.services.ozon_sync_service import ozon_auto_sync_loop, sync_ozon_all, get_ozon_status
except Exception as e:
    print(f"[startup] Ozon sync unavailable: {e}")
    ozon_auto_sync_loop = None
    sync_ozon_all = None
    get_ozon_status = None

try:
    from app.routes.wb_booking import booking_auto_check_loop
except Exception as e:
    print(f"[startup] Slot Hunter loop unavailable: {e}")
    booking_auto_check_loop = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = []

    try:
        run_lightweight_migrations()
        print("[startup] DB migrations completed")
    except Exception as e:
        print(f"[startup] DB migration error: {e}")

    if wb_auto_sync_loop and (getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")):
        tasks.append(asyncio.create_task(wb_auto_sync_loop()))
        print("[startup] WB auto sync loop started")

    if ozon_auto_sync_loop and settings.ozon_client_id and settings.ozon_api_key:
        tasks.append(asyncio.create_task(ozon_auto_sync_loop()))
        print("[startup] Ozon auto sync loop started")

    if autopublish_loop:
        tasks.append(asyncio.create_task(autopublish_loop()))
        print("[startup] autopublish loop started")

    if booking_auto_check_loop:
        tasks.append(asyncio.create_task(booking_auto_check_loop()))
        print("[startup] Slot Hunter auto-check loop started")

    yield

    for task in tasks:
        task.cancel()


app = FastAPI(title="KARATOV CX Hub", lifespan=lifespan)
generator = AnswerGenerator()


_CONNECTED_ROUTERS: list[str] = []
_SKIPPED_ROUTERS: dict[str, str] = {}


def include_router_safe(module_path: str):
    try:
        module = __import__(module_path, fromlist=["router"])
        app.include_router(module.router)
        _CONNECTED_ROUTERS.append(module_path)
        print(f"[router] connected: {module_path}")
    except Exception as e:
        _SKIPPED_ROUTERS[module_path] = str(e)
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
    "app.routes.marketplace_health",
    "app.routes.wb_booking",
    "app.routes.ops_history",
    "app.routes.operations",
]:
    include_router_safe(route)


def _diagnostics_payload(db: Session | None = None):
    counts = {}
    if db is not None:
        counts = {
            "reviews_total": db.query(Review).count(),
            "questions_total": db.query(Question).count(),
            "reviews_unanswered": db.query(Review).filter(Review.operational_status == "needs_response").count(),
            "questions_unanswered": db.query(Question).filter(Question.operational_status == "needs_response").count(),
            "wb_reviews": db.query(Review).filter(Review.platform == "WB").count(),
            "wb_questions": db.query(Question).filter(Question.platform == "WB").count(),
            "ozon_reviews": db.query(Review).filter(Review.platform == "OZON").count(),
            "ozon_questions": db.query(Question).filter(Question.platform == "OZON").count(),
            "seller_cabinet_answers_reviews": db.query(Review).filter(Review.response_origin == "seller_cabinet").count(),
            "seller_cabinet_answers_questions": db.query(Question).filter(Question.response_origin == "seller_cabinet").count(),
        }
    return {
        "status": "ok",
        "routers": {"connected": _CONNECTED_ROUTERS, "skipped": _SKIPPED_ROUTERS},
        "keys": {
            "openai_api_key": bool(settings.openai_api_key),
            "wb_api_key": bool(getattr(settings, "wb_api_key", "") or getattr(settings, "wb_api_token", "")),
            "wb_api_token": bool(getattr(settings, "wb_api_token", "") or getattr(settings, "wb_api_key", "")),
            "ozon_client_id": bool(settings.ozon_client_id),
            "ozon_api_key": bool(settings.ozon_api_key),
        },
        "openai": {"model": settings.openai_model},
        "publishing": {
            "enable_marketplace_publishing": bool(settings.enable_marketplace_publishing),
            "mode": "real_publish" if settings.enable_marketplace_publishing else "dry_run",
        },
        "counts": counts,
        "wb_sync": _safe_call(get_sync_status),
        "ozon_sync": _safe_call(get_ozon_status),
    }


def _safe_call(fn):
    if fn is None:
        return None
    try:
        return fn()
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/reviews")
def reviews_compat(
    platform: str | None = None,
    answer_state: str | None = None,
    sku: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    q = db.query(Review)
    if platform and platform.upper() != "ALL":
        q = q.filter(Review.platform == platform.upper())
    if sku:
        q = q.filter(Review.sku == sku)
    if answer_state == "unanswered":
        q = q.filter(Review.operational_status == "needs_response")
    elif answer_state == "answered":
        q = q.filter(Review.has_answer == True)  # noqa: E712
    rows = q.order_by(Review.created_at_marketplace.desc().nullslast(), Review.id.desc()).limit(min(limit, 5000)).all()
    return jsonable_encoder(rows)


@app.get("/questions")
def questions_compat(
    platform: str | None = None,
    answer_state: str | None = None,
    sku: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    q = db.query(Question)
    if platform and platform.upper() != "ALL":
        q = q.filter(Question.platform == platform.upper())
    if sku:
        q = q.filter(Question.sku == sku)
    if answer_state == "unanswered":
        q = q.filter(Question.operational_status == "needs_response")
    elif answer_state == "answered":
        q = q.filter(Question.has_answer == True)  # noqa: E712
    rows = q.order_by(Question.created_at_marketplace.desc().nullslast(), Question.id.desc()).limit(min(limit, 5000)).all()
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


@app.get("/sync/status")
def sync_status_compat():
    if get_sync_status is None:
        return JSONResponse(status_code=503, content={"error": "WB sync service unavailable"})
    return get_sync_status()


@app.get("/ozon-sync/status")
def ozon_sync_status_legacy_alias():
    if get_ozon_status is None:
        return JSONResponse(status_code=503, content={"error": "Ozon sync service unavailable"})
    return get_ozon_status()


@app.get("/sync/ozon/status")
def ozon_sync_status_compat():
    if get_ozon_status is None:
        return JSONResponse(status_code=503, content={"error": "Ozon sync service unavailable"})
    return get_ozon_status()


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


@app.get("/system/diagnostics")
def diagnostics_compat(db: Session = Depends(get_db)):
    return _diagnostics_payload(db)


@app.get("/system/status")
def system_status(db: Session = Depends(get_db)):
    return _diagnostics_payload(db)


@app.get("/ops/sync-history")
def sync_history_compat():
    return {"wb": _safe_call(get_sync_status), "ozon": _safe_call(get_ozon_status)}


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
        "api/", "system/", "sync/", "ozon-sync/", "settings/", "reports", "summary",
        "reviews", "questions", "generate", "autopublish", "wb-booking", "ops/",
        "operations", "analytics/", "marketplace-health",
    )
    if full_path.startswith(api_prefixes):
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"error": "Frontend not found"})
