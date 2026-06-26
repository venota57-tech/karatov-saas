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
    # RC1.7.3 GitHub Actions Sync Runner.
    # Web stays API/UI only. Regular marketplace exchange runs from GitHub Actions.
    print("[startup] HTTP-first mode: GitHub Actions runner owns heavy sync")
    yield


app = FastAPI(title="KARATOV CX Hub", lifespan=lifespan)
generator = AnswerGenerator()


def include_router_safe(module_path: str):
    try:
        module = __import__(module_path, fromlist=["router"])
        app.include_router(module.router)
        print(f"[router] connected: {module_path}")
    except Exception as e:
        print(f"[router] skipped {module_path}: {e}")

# KARATOV_MARKETPLACE_OS_PRIORITY_ROUTES_RC170
def _karatov_platform_aliases(platform: str | None):
    p = (platform or "ALL").strip().upper()
    if p in {"", "ALL"}:
        return None
    if p in {"WB", "WILDBERRIES", "WILDBERRY", "ВБ"}:
        return ["WB", "WILDBERRIES", "WILDBERRY", "ВБ"]
    if p in {"OZON", "OZON.RU", "ОЗОН"}:
        return ["OZON", "OZON.RU", "ОЗОН"]
    if p in {"YM", "YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС"}:
        return ["YM", "YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС"]
    return [p]


def _karatov_filter_platform(q, model, platform: str | None):
    aliases = _karatov_platform_aliases(platform)
    if aliases:
        return q.filter(model.platform.in_(aliases))
    return q


@app.get("/reviews")
def reviews_priority_rc170(
    platform: str | None = None,
    status: str | None = None,
    answer_state: str | None = None,
    source_status: str | None = None,
    product: str | None = None,
    category: str | None = None,
    risk: str | None = None,
    response_origin: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(Review)
    q = _karatov_filter_platform(q, Review, platform)

    if status and status != "all":
        q = q.filter(Review.status == status)
    if source_status:
        q = q.filter(Review.source_status == source_status)
    if product:
        like = f"%{product}%"
        q = q.filter((Review.sku.ilike(like)) | (Review.product_name.ilike(like)))
    if risk:
        q = q.filter(Review.ai_risk_level == risk)
    if response_origin:
        q = q.filter(Review.response_origin == response_origin)

    if answer_state and answer_state != "all":
        if answer_state == "unanswered":
            q = q.filter(Review.operational_status == "needs_response")
        elif answer_state == "answered":
            q = q.filter(Review.has_answer == True)  # noqa: E712
        elif answer_state == "no_text":
            q = q.filter(Review.operational_status == "no_text_rating")

    safe_limit = min(max(int(limit or 200), 1), 1000)
    safe_offset = max(int(offset or 0), 0)
    rows = q.order_by(Review.created_at_marketplace.desc(), Review.id.desc()).offset(safe_offset).limit(safe_limit).all()
    return jsonable_encoder(rows)


@app.get("/questions")
def questions_priority_rc170(
    platform: str | None = None,
    status: str | None = None,
    answer_state: str | None = None,
    source_status: str | None = None,
    product: str | None = None,
    category: str | None = None,
    risk: str | None = None,
    response_origin: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(Question)
    q = _karatov_filter_platform(q, Question, platform)

    if status and status != "all":
        q = q.filter(Question.status == status)
    if source_status:
        q = q.filter(Question.source_status == source_status)
    if product:
        like = f"%{product}%"
        q = q.filter((Question.sku.ilike(like)) | (Question.product_name.ilike(like)))
    if risk:
        q = q.filter(Question.ai_risk_level == risk)
    if response_origin:
        q = q.filter(Question.response_origin == response_origin)

    if answer_state and answer_state != "all":
        if answer_state == "unanswered":
            q = q.filter(Question.operational_status == "needs_response")
        elif answer_state == "answered":
            q = q.filter(Question.has_answer == True)  # noqa: E712

    safe_limit = min(max(int(limit or 200), 1), 1000)
    safe_offset = max(int(offset or 0), 0)
    rows = q.order_by(Question.created_at_marketplace.desc(), Question.id.desc()).offset(safe_offset).limit(safe_limit).all()
    return jsonable_encoder(rows)


for route in [
    "app.routes.sync_truth",
    "app.routes.sync_runner",
    "app.routes.cron",
    "app.routes.full_sync",
    "app.routes.marketplace_os",
    "app.routes.system",
    "app.routes.jobs",
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
    "app.routes.operations", "app.routes.sync_audit", "app.routes.ops_reporting", "app.routes.customer_ops", "app.routes.sync_control", "app.routes.stable_os",
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
    rows = q.order_by(Review.created_at_marketplace.desc().nullslast(), Review.id.desc()).limit(min(max(limit, 1), 500)).all()
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
    rows = q.order_by(Question.created_at_marketplace.desc().nullslast(), Question.id.desc()).limit(min(max(limit, 1), 500)).all()
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
        "autopublish", "wb-booking", "ops/", "operations", "analytics/", "stable-os", "sync-control/", "sync-audit",
    )
    if full_path.startswith(api_prefixes):
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"error": "Frontend not found"})
