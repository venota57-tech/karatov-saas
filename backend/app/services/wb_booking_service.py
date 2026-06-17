from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from sqlalchemy import text

from ..config import settings
from ..database import engine

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "mode": "monitor_only",  # monitor_only | reserve_draft | auto_book
    "warehouses": ["Коледино", "Электросталь"],
    "supply_type": "Суперсейф",
    "coefficient_limit": 20,
    "check_interval_seconds": 300,
    "workday_start_hour": 9,
    "workday_end_hour": 21,
    "target_every_working_days": 3,
    "lookahead_days": 45,
    "notify_users": [],
    "draft_strategy": "freshest_or_create",
    "dedupe_lock_minutes": 30,
}

_status: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "last_result": None,
    "last_matched_slots": [],
    "events": [],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _json_dumps(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, fallback: Any) -> Any:
    import json
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def ensure_booking_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wb_booking_settings (
                id INTEGER PRIMARY KEY,
                config JSON,
                updated_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wb_booking_events (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMP,
                event_type VARCHAR(64),
                status VARCHAR(64),
                warehouse VARCHAR(128),
                supply_date VARCHAR(64),
                coefficient VARCHAR(64),
                message TEXT,
                raw JSON
            )
        """))


def get_config() -> dict[str, Any]:
    ensure_booking_tables()
    with engine.begin() as conn:
        row = conn.execute(text("SELECT config FROM wb_booking_settings WHERE id = 1")).fetchone()
    cfg = dict(DEFAULT_CONFIG)
    if row:
        cfg.update(_json_loads(row[0] if not isinstance(row[0], dict) else _json_dumps(row[0]), {}))
    env_enabled = os.getenv("WB_BOOKING_ENABLED")
    if env_enabled is not None:
        cfg["enabled"] = env_enabled.strip().lower() in {"1", "true", "yes", "on"}
    return cfg


def save_config(incoming: dict[str, Any]) -> dict[str, Any]:
    ensure_booking_tables()
    cfg = get_config()
    cfg.update(incoming or {})
    if cfg.get("mode") not in {"monitor_only", "reserve_draft", "auto_book"}:
        cfg["mode"] = "monitor_only"
    cfg["coefficient_limit"] = int(cfg.get("coefficient_limit") or 20)
    cfg["check_interval_seconds"] = max(60, int(cfg.get("check_interval_seconds") or 300))
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO wb_booking_settings (id, config, updated_at)
                VALUES (1, CAST(:config AS JSON), :updated_at)
                ON CONFLICT (id) DO UPDATE SET config = CAST(:config AS JSON), updated_at = :updated_at
            """),
            {"config": _json_dumps(cfg), "updated_at": _now()},
        )
    return cfg


def _event(event_type: str, status: str, message: str, raw: Any = None, warehouse: str | None = None, supply_date: str | None = None, coefficient: Any = None) -> dict[str, Any]:
    ensure_booking_tables()
    item = {
        "created_at": _now_iso(),
        "event_type": event_type,
        "status": status,
        "warehouse": warehouse,
        "supply_date": supply_date,
        "coefficient": coefficient,
        "message": message,
        "raw": raw or {},
    }
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO wb_booking_events (created_at, event_type, status, warehouse, supply_date, coefficient, message, raw)
                VALUES (:created_at, :event_type, :status, :warehouse, :supply_date, :coefficient, :message, CAST(:raw AS JSON))
            """),
            {
                **item,
                "created_at": _now(),
                "coefficient": None if coefficient is None else str(coefficient),
                "raw": _json_dumps(raw or {}),
            },
        )
    _status.setdefault("events", []).insert(0, item)
    _status["events"] = _status["events"][:50]
    return item


def get_events(limit: int = 50) -> list[dict[str, Any]]:
    ensure_booking_tables()
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT created_at, event_type, status, warehouse, supply_date, coefficient, message, raw
            FROM wb_booking_events
            ORDER BY id DESC
            LIMIT :limit
        """), {"limit": limit}).fetchall()
    return [
        {
            "created_at": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            "event_type": r[1],
            "status": r[2],
            "warehouse": r[3],
            "supply_date": r[4],
            "coefficient": r[5],
            "message": r[6],
            "raw": r[7] if isinstance(r[7], dict) else _json_loads(r[7], {}),
        }
        for r in rows
    ]


def _target_dates(cfg: dict[str, Any]) -> set[str]:
    today = _now().date()
    out: set[str] = set()
    step = max(1, int(cfg.get("target_every_working_days") or 3))
    workday_counter = 0
    for i in range(0, int(cfg.get("lookahead_days") or 45)):
        d = today + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        if workday_counter % step == 0:
            out.add(d.isoformat())
        workday_counter += 1
    return out


def _in_work_window(cfg: dict[str, Any]) -> bool:
    # Render usually runs UTC. We keep the guard soft: if users need exact local time, set broad hours.
    hour = _now().hour
    return int(cfg.get("workday_start_hour", 0)) <= hour <= int(cfg.get("workday_end_hour", 23))


async def _fetch_acceptance_coefficients() -> list[dict[str, Any]]:
    token = settings.wb_api_token
    if not token:
        raise RuntimeError("WB_API_KEY/WB_API_TOKEN не найден")

    url = os.getenv("WB_ACCEPTANCE_COEFFICIENTS_URL", "https://common-api.wildberries.ru/api/v1/tariffs/acceptance/coefficients")
    headers = {"Authorization": token}
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(url, headers=headers)
        if res.status_code == 404:
            raise RuntimeError("WB endpoint коэффициентов не найден. Укажи актуальный WB_ACCEPTANCE_COEFFICIENTS_URL в Render.")
        if res.status_code == 429:
            raise RuntimeError("WB вернул 429 по коэффициентам приемки. Slot Hunter поставлен на паузу до следующего цикла.")
        if res.status_code >= 400:
            raise RuntimeError(f"WB coefficients API вернул {res.status_code}: {res.text[:500]}")
        payload = res.json()

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "result", "coefficients"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _extract_slot(row: dict[str, Any]) -> dict[str, Any]:
    warehouse = row.get("warehouseName") or row.get("warehouse_name") or row.get("warehouse") or row.get("boxDeliveryWarehouseName") or ""
    date = row.get("date") or row.get("deliveryDate") or row.get("supplyDate") or row.get("acceptanceDate") or ""
    coefficient = row.get("coefficient") or row.get("boxDeliveryCoefExpr") or row.get("boxDeliveryCoefficient") or row.get("coef")
    try:
        coefficient_num = float(str(coefficient).replace(",", "."))
    except Exception:
        coefficient_num = None
    return {
        "warehouse": str(warehouse),
        "date": str(date)[:10] if date else "",
        "coefficient": coefficient,
        "coefficient_num": coefficient_num,
        "raw": row,
    }


def _score_slot(slot: dict[str, Any], cfg: dict[str, Any], targets: set[str]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    wh = slot.get("warehouse", "").lower()
    wanted = [str(x).lower() for x in cfg.get("warehouses", [])]
    if any(x in wh for x in wanted):
        score += 40
        reasons.append("подходящий склад")
    if slot.get("date") in targets:
        score += 30
        reasons.append("подходящая дата")
    coef = slot.get("coefficient_num")
    if coef is not None and coef <= float(cfg.get("coefficient_limit", 20)):
        score += 30
        reasons.append("коэффициент в лимите")
    return score, reasons


async def check_slots_once(source: str = "manual") -> dict[str, Any]:
    cfg = get_config()
    _status["running"] = True
    _status["last_started_at"] = _now_iso()
    _status["last_error"] = None

    try:
        if not _in_work_window(cfg):
            result = {"ok": True, "skipped": True, "reason": "outside_work_window", "config": cfg}
            _status["last_result"] = result
            return result

        rows = await _fetch_acceptance_coefficients()
        targets = _target_dates(cfg)
        slots = [_extract_slot(x) for x in rows]
        matched = []
        for slot in slots:
            score, reasons = _score_slot(slot, cfg, targets)
            if score >= 70:
                matched.append({**slot, "score": score, "reasons": reasons})
        matched.sort(key=lambda x: (-x["score"], x.get("date") or "9999-99-99"))

        action = "notify"
        if matched:
            best = matched[0]
            if cfg.get("mode") == "monitor_only":
                msg = f"Найден подходящий слот: {best.get('warehouse')} {best.get('date')} коэффициент {best.get('coefficient')}"
                _event("slot_found", "monitor_only", msg, best, best.get("warehouse"), best.get("date"), best.get("coefficient"))
            elif cfg.get("mode") in {"reserve_draft", "auto_book"}:
                # Без официального подтвержденного endpoint бронирования не создаем поставку вслепую.
                msg = "Слот найден, но авто-бронирование не выполнено: требуется подключить подтвержденный WB endpoint создания/резерва поставки."
                _event("slot_found", "needs_booking_endpoint", msg, best, best.get("warehouse"), best.get("date"), best.get("coefficient"))
                action = "needs_booking_endpoint"
        else:
            _event("check", "no_slots", "Подходящих слотов по заданным правилам нет", {"received": len(rows), "targets": sorted(targets)[:10]})

        result = {
            "ok": True,
            "source": source,
            "mode": cfg.get("mode"),
            "action": action,
            "received": len(rows),
            "matched_count": len(matched),
            "matched_slots": matched[:20],
            "config": cfg,
        }
        _status["last_result"] = result
        _status["last_matched_slots"] = matched[:20]
        return result
    except Exception as exc:
        _status["last_error"] = str(exc)
        _event("check", "error", str(exc), {"source": source})
        return {"ok": False, "error": str(exc), "config": cfg}
    finally:
        _status["running"] = False
        _status["last_finished_at"] = _now_iso()


def get_status() -> dict[str, Any]:
    cfg = get_config()
    return {
        **_status,
        "enabled": bool(cfg.get("enabled")),
        "config": cfg,
        "events": get_events(30),
        "market_logic": {
            "modes": ["monitor_only", "reserve_draft", "auto_book"],
            "recommended_mode_now": "monitor_only",
            "reason": "Автобронирование включать только после подтверждения рабочего WB endpoint создания/резерва поставки и защиты от дублей.",
        },
    }


async def wb_booking_loop() -> None:
    await asyncio.sleep(30)
    while True:
        cfg = get_config()
        if cfg.get("enabled"):
            await check_slots_once(source="auto")
        await asyncio.sleep(max(60, int(cfg.get("check_interval_seconds") or 300)))
