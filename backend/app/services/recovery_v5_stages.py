from __future__ import annotations

import asyncio
import os
import time
from typing import Any


def _print(message: str, payload: Any | None = None) -> None:
    if payload is None:
        print(f"[recovery-v5-stage] {message}", flush=True)
    else:
        print(f"[recovery-v5-stage] {message}: {payload}", flush=True)


def _raw(svc: Any, platform: str, block: str, status: str, payload: Any, error: str | None = None) -> None:
    try:
        svc.raw(platform, block, status, payload, error)
    except Exception as exc:
        print(f"[recovery-v5-stage] raw log failed: {exc}", flush=True)


def _normalize(stage: str, platform: str, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "ok": False,
            "stage": stage,
            "platform": platform,
            "received": 0,
            "errors": [f"stage returned non-dict result: {result!r}"],
            "warnings": [],
            "raw_result": result,
        }

    out = dict(result)
    errors = [str(e) for e in (out.get("errors") or []) if e]
    warnings = [str(w) for w in (out.get("warnings") or []) if w]

    received = 0
    try:
        received = int(out.get("received") or 0)
    except Exception:
        received = 0

    # Some composite stages return parts.
    parts = out.get("parts") or []
    if isinstance(parts, list) and parts:
        part_received = 0
        part_errors = []
        for p in parts:
            if isinstance(p, dict):
                try:
                    part_received += int(p.get("received") or 0)
                except Exception:
                    pass
                part_errors.extend([str(e) for e in (p.get("errors") or []) if e])
        received = max(received, part_received)
        errors.extend(part_errors)

    if out.get("ok") is False:
        ok = False
    elif errors and received == 0:
        ok = False
    else:
        ok = True

    if received == 0 and not errors and stage not in {"scheduler_heartbeat", "heartbeat"}:
        warnings.append("stage returned 0 items; this may be valid for an incremental hot run, but is suspicious for deep backfill")

    out["ok"] = ok
    out["stage"] = out.get("stage") or stage
    out["platform"] = out.get("platform") or platform
    out["received"] = received
    out["errors"] = errors
    out["warnings"] = warnings
    out["partial"] = bool(errors and received > 0)
    return out


async def _run_single_stage(svc: Any, stage: str, platform: str, deep: bool) -> dict[str, Any]:
    stage = (stage or "").lower()
    platform = (platform or "ALL").upper()

    if stage in {"scheduler_heartbeat", "heartbeat"}:
        _raw(svc, "ALL", "scheduler_heartbeat", "success", {"stage": stage, "platform": platform, "source": "github_actions"})
        return {"ok": True, "stage": stage, "platform": platform, "received": 1, "heartbeat": "saved", "errors": []}

    if stage in {"wb_reviews_questions", "wb_reviews", "wb_questions"}:
        if platform not in {"ALL", "WB"}:
            return {"ok": True, "stage": stage, "platform": platform, "received": 0, "skipped": "platform is not WB/ALL", "errors": []}
        return _normalize(stage, platform, await svc.wb_reviews_questions(deep=deep))

    if stage in {"ozon_reviews", "ozon_reviews_questions", "ozon_questions"}:
        if platform not in {"ALL", "OZON"}:
            return {"ok": True, "stage": stage, "platform": platform, "received": 0, "skipped": "platform is not OZON/ALL", "errors": []}
        return _normalize(stage, platform, await svc.ozon_reviews_questions(deep=deep))

    if stage in {"reviews_questions", "reviews", "questions", "answers"}:
        parts = []
        if platform in {"ALL", "WB"}:
            parts.append(_normalize("wb_reviews_questions", "WB", await svc.wb_reviews_questions(deep=deep)))
        if platform in {"ALL", "OZON"}:
            parts.append(_normalize("ozon_reviews", "OZON", await svc.ozon_reviews_questions(deep=deep)))
        received = sum(int(p.get("received", 0) or 0) for p in parts)
        errors = []
        for p in parts:
            errors.extend(p.get("errors") or [])
        return {
            "ok": any(p.get("ok") for p in parts) and not (errors and received == 0),
            "stage": stage,
            "platform": platform,
            "parts": parts,
            "received": received,
            "errors": errors,
            "partial": bool(errors and received > 0),
        }

    if stage in {"chats", "customer_ops"}:
        return _normalize(stage, platform, await svc.chats(platform))

    if stage in {"operations", "ops"}:
        return _normalize(stage, platform, await svc.operations(platform))

    return {"ok": False, "stage": stage, "platform": platform, "received": 0, "errors": [f"Unknown recovery stage: {stage}"]}


async def run_stage(svc: Any, stage: str, platform: str = "ALL", deep: bool = False) -> dict[str, Any]:
    stage = (stage or "all_safe").lower()
    platform = (platform or "ALL").upper()
    timeout = int(os.getenv("RECOVERY_STAGE_TIMEOUT_SECONDS", "1200"))
    started = time.time()

    try:
        svc.ensure()
    except Exception:
        pass

    _print("START", {"stage": stage, "platform": platform, "deep": deep, "timeout": timeout})
    _raw(svc, platform, "stage_start", "success", {"stage": stage, "platform": platform, "deep": deep, "timeout": timeout})

    if stage in {"all", "all_safe", "full"}:
        stages = []
        if platform in {"ALL", "WB"}:
            stages.append("wb_reviews_questions")
        if platform in {"ALL", "OZON"}:
            stages.append("ozon_reviews")
        stages.extend(["chats", "operations"])

        parts = []
        for one in stages:
            one_start = time.time()
            _print("STAGE_BEGIN", {"stage": one})
            _raw(svc, platform, "stage_begin", "success", {"stage": one, "parent": stage})
            try:
                result = await asyncio.wait_for(_run_single_stage(svc, one, platform, deep), timeout=timeout)
                result = _normalize(one, platform, result)
                part = {"stage": one, "ok": result.get("ok", False), "seconds": round(time.time() - one_start, 2), "result": result}
                parts.append(part)
                _print("STAGE_RESULT", {"stage": one, "ok": result.get("ok"), "received": result.get("received"), "errors": result.get("errors"), "warnings": result.get("warnings")})
                _raw(svc, platform, "stage_result", "success" if result.get("ok") else "failed", part, None if result.get("ok") else "; ".join(result.get("errors") or []))
            except asyncio.TimeoutError:
                err = f"stage {one} timed out after {timeout}s"
                part = {"stage": one, "ok": False, "seconds": round(time.time() - one_start, 2), "error": err}
                parts.append(part)
                _print("STAGE_TIMEOUT", part)
                _raw(svc, platform, "stage_timeout", "failed", part, err)
            except Exception as exc:
                part = {"stage": one, "ok": False, "seconds": round(time.time() - one_start, 2), "error": str(exc)}
                parts.append(part)
                _print("STAGE_ERROR", part)
                _raw(svc, platform, "stage_error", "failed", part, str(exc))

        ok = any(p.get("ok") for p in parts)
        final = {
            "ok": ok,
            "stage": stage,
            "platform": platform,
            "deep": deep,
            "seconds": round(time.time() - started, 2),
            "parts": parts,
        }
        _raw(svc, platform, "stage_finish", "success" if ok else "failed", final, None if ok else "all_safe failed")
        _print("FINISH", final)
        return final

    try:
        result = await asyncio.wait_for(_run_single_stage(svc, stage, platform, deep), timeout=timeout)
        result = _normalize(stage, platform, result)
        final = {
            "ok": result.get("ok", False),
            "stage": stage,
            "platform": platform,
            "deep": deep,
            "seconds": round(time.time() - started, 2),
            "received": result.get("received", 0),
            "errors": result.get("errors", []),
            "warnings": result.get("warnings", []),
            "result": result,
        }
        _print("STAGE_RESULT", final)
        _raw(svc, platform, "stage_finish", "success" if final["ok"] else "failed", final, None if final["ok"] else "; ".join(final.get("errors") or []))
        return final
    except asyncio.TimeoutError:
        err = f"stage {stage} timed out after {timeout}s"
        final = {"ok": False, "stage": stage, "platform": platform, "error": err}
        _raw(svc, platform, "stage_timeout", "failed", final, err)
        _print("TIMEOUT", final)
        return final
    except Exception as exc:
        final = {"ok": False, "stage": stage, "platform": platform, "error": str(exc)}
        _raw(svc, platform, "stage_error", "failed", final, str(exc))
        _print("ERROR", final)
        return final
