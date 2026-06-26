from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session

from app.database import get_db, engine
from app.models import Question, Review, SyncCursor, SyncJob

router = APIRouter(prefix="/sync-audit", tags=["sync-audit"])


def _dt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _model_totals(db: Session, model: Any, platform: str) -> dict[str, Any]:
    q = db.query(model)
    if platform != "ALL":
        q = q.filter(model.platform == platform)

    total = q.count()
    needs = q.filter(model.operational_status == "needs_response").count()
    answered = q.filter(model.has_answer == True).count()  # noqa: E712
    no_text = q.filter(model.operational_status == "no_text_rating").count() if model is Review else 0

    agg = q.with_entities(
        func.min(model.created_at_marketplace),
        func.max(model.created_at_marketplace),
        func.min(model.created_at),
        func.max(model.created_at),
        func.max(model.updated_at),
    ).first()

    source_rows = (
        q.with_entities(model.source_status, func.count(model.id))
        .group_by(model.source_status)
        .order_by(func.count(model.id).desc())
        .all()
    )
    operational_rows = (
        q.with_entities(model.operational_status, func.count(model.id))
        .group_by(model.operational_status)
        .order_by(func.count(model.id).desc())
        .all()
    )

    return {
        "total": total,
        "needs_response": needs,
        "answered": answered,
        "no_text_rating": no_text,
        "marketplace_date_min": _dt(agg[0] if agg else None),
        "marketplace_date_max": _dt(agg[1] if agg else None),
        "local_created_min": _dt(agg[2] if agg else None),
        "local_created_max": _dt(agg[3] if agg else None),
        "local_updated_max": _dt(agg[4] if agg else None),
        "by_source_status": {str(k or "null"): int(v) for k, v in source_rows},
        "by_operational_status": {str(k or "null"): int(v) for k, v in operational_rows},
    }


def _latest_rows(db: Session, model: Any, kind: str, platform: str, limit: int) -> list[dict[str, Any]]:
    q = db.query(model)
    if platform != "ALL":
        q = q.filter(model.platform == platform)
    rows = (
        q.order_by(model.created_at_marketplace.desc().nullslast(), model.id.desc())
        .limit(limit)
        .all()
    )
    out = []
    for row in rows:
        out.append(
            {
                "kind": kind,
                "id": row.id,
                "platform": row.platform,
                "external_id": row.external_id,
                "sku": row.sku,
                "product_name": row.product_name,
                "rating": getattr(row, "rating", None),
                "text": getattr(row, "text", None),
                "source_status": getattr(row, "source_status", None),
                "operational_status": getattr(row, "operational_status", None),
                "has_answer": getattr(row, "has_answer", None),
                "created_at_marketplace": _dt(getattr(row, "created_at_marketplace", None)),
                "created_at": _dt(getattr(row, "created_at", None)),
                "updated_at": _dt(getattr(row, "updated_at", None)),
            }
        )
    return out


def _sync_jobs(db: Session, limit: int = 30) -> list[dict[str, Any]]:
    rows = (
        db.query(SyncJob)
        .order_by(SyncJob.created_at.desc().nullslast(), SyncJob.id.desc())
        .limit(limit)
        .all()
    )
    out = []
    for j in rows:
        result = _json(getattr(j, "result", None))
        summary: dict[str, Any] = {}
        if isinstance(result, dict):
            summary = {
                "kind": result.get("kind"),
                "received": result.get("received"),
                "created": result.get("created"),
                "updated": result.get("updated"),
                "has_delta": result.get("has_delta"),
                "stages": {},
            }
            stages = result.get("stages") or {}
            if isinstance(stages, dict):
                for name, stage in stages.items():
                    sr = stage.get("result") if isinstance(stage, dict) else None
                    if isinstance(sr, dict):
                        summary["stages"][name] = {
                            "ok": stage.get("ok"),
                            "received": sr.get("received"),
                            "created": sr.get("created"),
                            "updated": sr.get("updated"),
                            "has_delta": sr.get("has_delta"),
                            "blocks": [
                                {
                                    "stage": b.get("stage"),
                                    "block": b.get("block"),
                                    "status": b.get("status"),
                                    "received": b.get("received"),
                                    "created": b.get("created"),
                                    "updated": b.get("updated"),
                                    "finish_last_id": b.get("finish_last_id"),
                                    "error": b.get("error"),
                                }
                                for b in (sr.get("blocks") or [])[:12]
                                if isinstance(b, dict)
                            ],
                        }
        out.append(
            {
                "id": j.id,
                "job_type": j.job_type,
                "platform": j.platform,
                "block": j.block,
                "status": j.status,
                "started_at": _dt(j.started_at),
                "finished_at": _dt(j.finished_at),
                "created_at": _dt(j.created_at),
                "last_error": j.last_error,
                "summary": summary,
            }
        )
    return out


def _cursors(db: Session) -> list[dict[str, Any]]:
    try:
        rows = db.query(SyncCursor).order_by(SyncCursor.platform, SyncCursor.block).all()
    except Exception as exc:
        return [{"error": str(exc)}]
    out = []
    for c in rows:
        out.append(
            {
                "platform": c.platform,
                "block": c.block,
                "cursor": c.cursor,
                "status": c.status,
                "last_success_at": _dt(c.last_success_at),
                "updated_at": _dt(c.updated_at),
                "last_error": c.last_error,
                "payload": _json(c.payload),
            }
        )
    return out


def _raw_events(db: Session, platform: str) -> list[dict[str, Any]]:
    try:
        tables = inspect(engine).get_table_names()
        if "marketplace_raw_events" not in tables:
            return []
        rows = db.execute(
            text(
                "SELECT platform, block, status, error, created_at, updated_at "
                "FROM marketplace_raw_events "
                "WHERE (:platform='ALL' OR platform=:platform) "
                "ORDER BY created_at DESC LIMIT 30"
            ),
            {"platform": platform},
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"error": str(exc)}]


@router.get("/marketplace")
def marketplace_sync_audit(platform: str = "ALL", latest_limit: int = 20, db: Session = Depends(get_db)):
    platform = (platform or "ALL").upper()
    latest_limit = min(max(int(latest_limit or 20), 1), 100)

    reviews = _model_totals(db, Review, platform)
    questions = _model_totals(db, Question, platform)

    latest = _latest_rows(db, Review, "review", platform, latest_limit) + _latest_rows(db, Question, "question", platform, latest_limit)
    latest.sort(key=lambda x: x.get("created_at_marketplace") or x.get("created_at") or "", reverse=True)

    diagnosis = []
    if platform in {"ALL", "OZON"}:
        diagnosis.append("Ozon Hot Sync is a fresh-queue updater, not full historical backfill. Historical growth depends on Backfill/Nightly and cursor progress.")
    if platform in {"ALL", "WB"}:
        diagnosis.append("WB Hot Sync pulls fast blocks. Answered/archive growth depends on Backfill/Nightly; if created=0, check WB page cursor and 429 errors.")
    diagnosis.append("If GitHub Action is green but created=0 and updated>0, exchange works but marketplace returned rows already present in DB.")
    diagnosis.append("If marketplace_date_max is fresh but Control Tower showed older rows, the cause was frontend feed slicing before global date sort.")

    return jsonable_encoder(
        {
            "ok": True,
            "platform": platform,
            "reviews": reviews,
            "questions": questions,
            "combined_total": int(reviews["total"]) + int(questions["total"]),
            "latest": latest[:latest_limit],
            "sync_jobs": _sync_jobs(db),
            "sync_cursors": _cursors(db),
            "raw_events": _raw_events(db, platform),
            "frontend_limits": {
                "reviews_endpoint_max_limit": 1000,
                "questions_endpoint_max_limit": 1000,
                "old_control_tower_bug": "buildEventFeed sliced reviews/questions before global date sort; reviews could push out newer questions.",
            },
            "diagnosis": diagnosis,
        }
    )
