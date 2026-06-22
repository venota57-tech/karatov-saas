from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.database import engine


READY_STATUSES = ("ready_to_review", "ready_to_publish", "answer_rejected_quality_gate", "publish_dry_run")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _platform(value: str | None) -> str:
    value = (value or "ALL").strip().upper()
    if value in {"WILDBERRIES", "WILDBERRY", "ВБ"}:
        return "WB"
    if value in {"OZON.RU", "ОЗОН"}:
        return "OZON"
    if value in {"YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС"}:
        return "YM"
    if value in {"ALL", "WB", "OZON", "YM"}:
        return value
    return value


def _aliases(platform: str) -> list[str]:
    platform = _platform(platform)
    if platform == "ALL":
        return []
    if platform == "WB":
        return ["WB", "WILDBERRIES", "WILDBERRY", "ВБ", "wildberries"]
    if platform == "OZON":
        return ["OZON", "OZON.RU", "ОЗОН", "ozon"]
    if platform == "YM":
        return ["YM", "YANDEX", "YANDEX_MARKET", "ЯМ", "ЯНДЕКС", "yandex"]
    return [platform]


def _quote(values: list[str]) -> str:
    return ", ".join("'" + str(v).upper().replace("'", "''") + "'" for v in values)


def _where(platform: str, extra: str | None = None) -> str:
    parts = []
    aliases = _aliases(platform)
    if aliases:
        parts.append(f"UPPER(platform) IN ({_quote(aliases)})")
    if extra:
        parts.append(f"({extra})")
    return " WHERE " + " AND ".join(parts) if parts else ""


def _set_fast_timeout(conn) -> None:
    if engine.dialect.name == "postgresql":
        conn.execute(text("SET statement_timeout TO 2500"))
        conn.execute(text("SET lock_timeout TO 1000"))


def _scalar(sql: str, default: Any = 0) -> Any:
    try:
        with engine.connect() as conn:
            _set_fast_timeout(conn)
            return conn.execute(text(sql)).scalar()
    except Exception:
        return default


def _count(table: str, platform: str, extra: str | None = None) -> int:
    value = _scalar(f"SELECT COUNT(id) FROM {table}{_where(platform, extra)}", 0)
    try:
        return int(value or 0)
    except Exception:
        return 0


def _avg_rating(platform: str) -> float | None:
    value = _scalar(f"SELECT AVG(rating) FROM reviews{_where(platform, 'rating IS NOT NULL')}", None)
    try:
        return round(float(value), 2) if value is not None else None
    except Exception:
        return None


def _products_total(platform: str) -> int | None:
    if _platform(platform) == "YM":
        return 0

    rw = _where(platform)
    qw = _where(platform)
    sql = f"""
    SELECT COUNT(*) FROM (
      SELECT DISTINCT COALESCE(NULLIF(sku, ''), NULLIF(product_name, '')) AS product_key FROM reviews{rw}
      UNION
      SELECT DISTINCT COALESCE(NULLIF(sku, ''), NULLIF(product_name, '')) AS product_key FROM questions{qw}
    ) x
    WHERE product_key IS NOT NULL
    """
    value = _scalar(sql, None)
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def build_dashboard(db=None, platform: str | None = "ALL") -> dict[str, Any]:
    p = _platform(platform)

    if p == "YM":
        counts = {
            "reviews_total": 0,
            "questions_total": 0,
            "communications_total": 0,
            "reviews_unanswered": 0,
            "questions_unanswered": 0,
            "needs_response": 0,
            "ready_to_publish": 0,
            "high_risk": 0,
            "no_text_reviews": 0,
            "avg_rating": None,
            "products_total": 0,
            "quality_attention": 0,
            "operations_total": 0,
            "operations_by_type": {},
        }
        return {
            "ok": True,
            "status": "not_connected",
            "platform": p,
            "generated_at": _now(),
            "source": "server_fast_counts",
            "marketplace_state": "not_connected",
            "counts": counts,
        }

    reviews_total = _count("reviews", p)
    questions_total = _count("questions", p)
    reviews_unanswered = _count("reviews", p, "operational_status = 'needs_response'")
    questions_unanswered = _count("questions", p, "operational_status = 'needs_response'")

    ready_expr = "status IN (" + ", ".join("'" + x + "'" for x in READY_STATUSES) + ")"
    ready_to_publish = _count("reviews", p, ready_expr) + _count("questions", p, ready_expr)

    high_risk = _count("reviews", p, "ai_risk_level = 'high'") + _count("questions", p, "ai_risk_level = 'high'")

    no_text_reviews = 0
    if p in {"ALL", "OZON"}:
        no_text_reviews = _count(
            "reviews",
            "OZON",
            "operational_status = 'no_text_rating' OR ((text IS NULL OR text = '') AND (pros IS NULL OR pros = '') AND (cons IS NULL OR cons = ''))",
        )

    products_total = _products_total(p)
    quality_attention = high_risk + no_text_reviews

    counts = {
        "reviews_total": reviews_total,
        "questions_total": questions_total,
        "communications_total": reviews_total + questions_total,
        "reviews_unanswered": reviews_unanswered,
        "questions_unanswered": questions_unanswered,
        "needs_response": reviews_unanswered + questions_unanswered,
        "ready_to_publish": ready_to_publish,
        "high_risk": high_risk,
        "no_text_reviews": no_text_reviews,
        "avg_rating": _avg_rating(p),
        "products_total": products_total,
        "quality_attention": quality_attention,
        "operations_total": None,
        "operations_by_type": {},
    }

    return {
        "ok": True,
        "status": "ok",
        "platform": p,
        "generated_at": _now(),
        "source": "server_fast_counts",
        "marketplace_state": "connected" if p in {"ALL", "WB", "OZON"} else "unknown",
        "counts": counts,
        "note": "Dashboard uses real fast SQL counters with DB statement timeout. Heavy Product Summary, Quality Hub and Operations load separately.",
    }


def build_dashboard_snapshot(db=None, platform: str | None = "ALL") -> dict[str, Any]:
    return build_dashboard(db, platform)


def refresh_dashboard(db=None) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "noop",
        "source": "server_fast_counts",
        "generated_at": _now(),
        "message": "Dashboard is calculated by fast counters on demand; no blocking refresh job is required.",
    }
