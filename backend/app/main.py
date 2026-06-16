from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import os
from typing import Any

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.config import settings
from app.database import Base, engine, run_lightweight_migrations, get_db
from app.models import Review, Question
from app.ai.answer_generator import AnswerGenerator
from app.services.autopublish_service import autopublish_once

try:
    from app.services.sync_service import wb_auto_sync_loop, get_sync_status
except Exception as e:  # noqa: BLE001
    print(f"[startup] WB sync service unavailable: {e}")
    wb_auto_sync_loop = None
    get_sync_status = None

try:
    from app.services.ozon_sync_service import ozon_auto_sync_loop, get_ozon_status
except Exception as e:  # noqa: BLE001
    print(f"[startup] Ozon sync service unavailable: {e}")
    ozon_auto_sync_loop = None
    get_ozon_status = None


def _init_db() -> None:
    try:
        Base.metadata.create_all(bind=engine)
        run_lightweight_migrations()
        print("[startup] database initialized")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] database init failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    tasks: list[asyncio.Task[Any]] = []

    if wb_auto_sync_loop and getattr(settings, "wb_api_token", None):
        print("[startup] starting WB auto sync loop")
        tasks.append(asyncio.create_task(wb_auto_sync_loop()))

    if ozon_auto_sync_loop and getattr(settings, "ozon_sync_enabled", False):
        print("[startup] starting Ozon auto sync loop")
        tasks.append(asyncio.create_task(ozon_auto_sync_loop()))

    yield

    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="KARATOV CX Hub", lifespan=lifespan)


def include_router_safe(module_path: str, router_name: str = "router") -> None:
    try:
        module = __import__(module_path, fromlist=[router_name])
        router = getattr(module, router_name)
        app.include_router(router)
        print(f"[router] connected: {module_path}")
    except Exception as e:  # noqa: BLE001
        print(f"[router] skipped {module_path}: {e}")


# Подключаем все реальные backend-модули локальной версии.
include_router_safe("app.routes.reviews")
include_router_safe("app.routes.questions")
include_router_safe("app.routes.reports")
include_router_safe("app.routes.summary")
include_router_safe("app.routes.settings")
include_router_safe("app.routes.autopublish_settings")
include_router_safe("app.routes.sync")
include_router_safe("app.routes.analytics")
include_router_safe("app.routes.ozon_sync")
include_router_safe("app.routes.system")
include_router_safe("app.routes.wb_booking")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/system/status")
def system_status():
    sync = None
    ozon = None

    if get_sync_status:
        try:
            sync = get_sync_status()
        except Exception as e:  # noqa: BLE001
            sync = {"error": str(e)}

    if get_ozon_status:
        try:
            ozon = get_ozon_status()
        except Exception as e:  # noqa: BLE001
            ozon = {"error": str(e)}

    return {
        "status": "ok",
        "keys": {
            "openai_api_key": bool(getattr(settings, "openai_api_key", None)),
            "wb_api_token": bool(getattr(settings, "wb_api_token", None)),
            "ozon_client_id": bool(getattr(settings, "ozon_client_id", None)),
            "ozon_api_key": bool(getattr(settings, "ozon_api_key", None)),
        },
        "openai": {"model": getattr(settings, "openai_model", None)},
        "publishing": {
            "enable_marketplace_publishing": bool(getattr(settings, "enable_marketplace_publishing", False)),
            "mode": "real_publish" if getattr(settings, "enable_marketplace_publishing", False) else "dry_run",
        },
        "sync": sync,
        "ozon": ozon,
    }


# Совместимость с текущим фронтом: короткие endpoints без префикса.
@app.get("/reviews")
def reviews_compat(
    status: str | None = None,
    platform: str | None = None,
    answer_state: str = "all",
    source_status: str | None = None,
    product: str | None = None,
    category: str | None = None,
    risk: str | None = None,
    response_origin: str | None = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    q = db.query(Review)
    if status:
        q = q.filter(Review.status == status)
    if platform:
        q = q.filter(Review.platform == platform)
    if source_status:
        q = q.filter(Review.source_status == source_status)
    if product:
        like = f"%{product}%"
        q = q.filter((Review.sku == product) | (Review.product_name.ilike(like)) | (Review.external_id == product))
    if category:
        q = q.filter(Review.ai_category == category)
    if risk:
        q = q.filter(Review.ai_risk_level == risk)
    if response_origin:
        q = q.filter(Review.response_origin == response_origin)
    if answer_state == "answered":
        q = q.filter(Review.source_status.in_(["wb_answered", "wb_archive", "ozon_answered"]))
    elif answer_state == "unanswered":
        q = q.filter(Review.operational_status == "needs_response", Review.source_status.in_(["wb_unanswered", "ozon_unanswered"]))
    elif answer_state == "stale":
        q = q.filter(Review.operational_status == "stale_unanswered")
    elif answer_state == "manual":
        q = q.filter(Review.status.in_(["ready_to_review", "ready_to_publish", "answer_rejected_quality_gate", "publish_dry_run"]))
    elif answer_state == "auto_published":
        q = q.filter(Review.status.in_(["auto_published", "published"]))
    rows = q.order_by(desc(Review.created_at_marketplace), desc(Review.created_at)).limit(min(max(limit, 1), 1000)).all()
    return jsonable_encoder(rows)


@app.get("/questions-flat")
def questions_flat(db: Session = Depends(get_db)):
    rows = db.query(Question).order_by(desc(Question.created_at_marketplace), desc(Question.created_at)).limit(500).all()
    return jsonable_encoder(rows)


@app.post("/generate")
async def generate_answer(req: Request, db: Session = Depends(get_db)):
    data = await req.json()
    try:
        from app.services.automation_rules import get_rules, apply_publication_rules
        rules = get_rules(db).rules or {}
        generator = AnswerGenerator(rules)
        if "rating" in data:
            result = generator.generate_for_review_until_pass(data)
            return apply_publication_rules(result, "review", data.get("rating"), db)
        result = generator.generate_for_question_until_pass(data)
        return apply_publication_rules(result, "question", None, db)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/autopublish")
async def autopublish():
    try:
        return await autopublish_once()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": str(e)})


frontend_path = os.path.join(os.path.dirname(__file__), "../frontend/dist")
assets_path = os.path.join(frontend_path, "assets")
index_path = os.path.join(frontend_path, "index.html")

if os.path.exists(assets_path):
    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    # React SPA fallback. API routes выше имеют приоритет.
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=500, content={"error": "frontend/dist/index.html не найден", "frontend_path": frontend_path})
