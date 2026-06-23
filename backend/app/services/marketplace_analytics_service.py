from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import MarketplaceOperation, Question, Review, SyncCursor, SyncJob


def _minutes(start, end) -> int | None:
    if not start or not end:
        return None
    diff = int((end - start).total_seconds() // 60)
    return diff if diff >= 0 else None


def _p90(values: list[int]) -> int | None:
    if not values:
        return None
    values = sorted(values)
    idx = max(0, min(len(values) - 1, int(len(values) * 0.9) - 1))
    return values[idx]


def _platform_filter(q, model, platform: str):
    p = (platform or "ALL").upper()
    if p == "ALL":
        return q
    return q.filter(model.platform == p)


def compute_sla(db: Session, platform: str = "ALL") -> dict[str, Any]:
    review_rows = _platform_filter(db.query(Review), Review, platform).filter(
        Review.created_at_marketplace.isnot(None),
        Review.answered_at.isnot(None),
    ).all()
    question_rows = _platform_filter(db.query(Question), Question, platform).filter(
        Question.created_at_marketplace.isnot(None),
        Question.answered_at.isnot(None),
    ).all()

    review_minutes = [x for x in (_minutes(r.created_at_marketplace, r.answered_at) for r in review_rows) if x is not None]
    question_minutes = [x for x in (_minutes(q.created_at_marketplace, q.answered_at) for q in question_rows) if x is not None]

    review_total = _platform_filter(db.query(Review), Review, platform).count()
    question_total = _platform_filter(db.query(Question), Question, platform).count()

    return {
        "platform": (platform or "ALL").upper(),
        "reviews": {
            "total": review_total,
            "answered_with_dates": len(review_minutes),
            "missing_answer_date": max(0, review_total - len(review_minutes)),
            "avg_minutes": round(mean(review_minutes), 1) if review_minutes else None,
            "p90_minutes": _p90(review_minutes),
            "within_60m": sum(1 for x in review_minutes if x <= 60),
            "over_60m": sum(1 for x in review_minutes if x > 60),
        },
        "questions": {
            "total": question_total,
            "answered_with_dates": len(question_minutes),
            "missing_answer_date": max(0, question_total - len(question_minutes)),
            "avg_minutes": round(mean(question_minutes), 1) if question_minutes else None,
            "p90_minutes": _p90(question_minutes),
            "within_30m": sum(1 for x in question_minutes if x <= 30),
            "over_30m": sum(1 for x in question_minutes if x > 30),
        },
    }


def runner_status(db: Session) -> dict[str, Any]:
    jobs = (
        db.query(SyncJob)
        .filter(SyncJob.job_type.in_(["github_sync_runner", "github_sync_block"]))
        .order_by(SyncJob.created_at.desc())
        .limit(20)
        .all()
    )
    cursors = db.query(SyncCursor).order_by(SyncCursor.platform, SyncCursor.block).all()
    ops_total = db.query(func.count(MarketplaceOperation.id)).scalar() or 0

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "github_actions_sync_runner",
        "operations_total": int(ops_total),
        "sla": {
            "ALL": compute_sla(db, "ALL"),
            "WB": compute_sla(db, "WB"),
            "OZON": compute_sla(db, "OZON"),
        },
        "jobs": [
            {
                "id": j.id,
                "job_type": j.job_type,
                "platform": j.platform,
                "block": j.block,
                "status": j.status,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "last_error": j.last_error,
                "result": j.result,
            }
            for j in jobs
        ],
        "cursors": [
            {
                "platform": c.platform,
                "block": c.block,
                "cursor": c.cursor,
                "status": c.status,
                "last_error": c.last_error,
                "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                "payload": c.payload,
            }
            for c in cursors
        ],
    }
