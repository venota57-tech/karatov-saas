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


async def _run_single_stage(svc: Any, stage: str, platform: str, deep: bool) -> dict[str, Any]:
    stage = (stage or "").lower()
    platform = (platform or "ALL").upper()

    if stage in {"scheduler_heartbeat", "heartbeat"}:
        _raw(svc, "ALL", "scheduler_heartbeat", "success", {"stage": stage, "platform": platform, "source": "github_actions"})
        return {"ok": True, "stage": stage, "platform": platform, "heartbeat": "saved"}

    if stage in {"wb_reviews_questions", "wb_reviews", "wb_questions"}:
        if platform not in {"ALL", "WB"}:
            return {"ok": True, "stage": stage, "platform": platform, "skipped": "platform is not WB/ALL"}
        return await svc.wb_reviews_questions(deep=deep)

    if stage in {"ozon_reviews", "ozon_reviews_questions", "ozon_questions"}:
        if platform not in {"ALL", "OZON"}:
            return {"ok": True, "stage": stage, "platform": platform, "skipped": "platform is not OZON/ALL"}
        return await svc.ozon_reviews_questions(deep=deep)

    if stage in {"reviews_questions", "reviews", "questions", "answers"}:
        parts = []
        if platform in {"ALL", "WB"}:
            parts.append(await svc.wb_reviews_questions(deep=deep))
        if platform in {"ALL", "OZON"}:
            parts.append(await svc.ozon_reviews_questions(deep=deep))
        return {
            "ok": any(not p.get("errors") for p in parts) if parts else True,
            "stage": stage,
            "platform": platform,
            "parts": parts,
            "received": sum(int(p.get("received", 0) or 0) for p in parts),
        }

    if stage in {"chats", "customer_ops"}:
        return await svc.chats(platform)

    if stage in {"operations", "ops"}:
        return await svc.operations(platform)

    raise ValueError(f"Unknown recovery stage: {stage}")


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
                result = result or {"ok": True}
                parts.append({"stage": one, "ok": result.get("ok", True), "seconds": round(time.time() - one_start, 2), "result": result})
                _print("STAGE_DONE", {"stage": one, "seconds": round(time.time() - one_start, 2), "received": result.get("received")})
                _raw(svc, platform, "stage_done", "success", {"stage": one, "seconds": round(time.time() - one_start, 2), "result": result})
            except asyncio.TimeoutError:
                err = f"stage {one} timed out after {timeout}s"
                parts.append({"stage": one, "ok": False, "error": err})
                _print("STAGE_TIMEOUT", {"stage": one, "timeout": timeout})
                _raw(svc, platform, "stage_timeout", "failed", {"stage": one, "timeout": timeout}, err)
            except Exception as exc:
                parts.append({"stage": one, "ok": False, "error": str(exc)})
                _print("STAGE_ERROR", {"stage": one, "error": str(exc)})
                _raw(svc, platform, "stage_error", "failed", {"stage": one}, str(exc))

        final = {
            "ok": any(p.get("ok") for p in parts),
            "stage": stage,
            "platform": platform,
            "deep": deep,
            "seconds": round(time.time() - started, 2),
            "parts": parts,
        }
        _raw(svc, platform, "stage_finish", "success" if final["ok"] else "failed", final, None if final["ok"] else "all_safe failed")
        _print("FINISH", final)
        return final

    try:
        result = await asyncio.wait_for(_run_single_stage(svc, stage, platform, deep), timeout=timeout)
        result = result or {"ok": True}
        final = {"ok": result.get("ok", True), "stage": stage, "platform": platform, "deep": deep, "seconds": round(time.time() - started, 2), "result": result}
        _raw(svc, platform, "stage_finish", "success" if final["ok"] else "failed", final, None if final["ok"] else "stage failed")
        _print("FINISH", final)
        return final
    except asyncio.TimeoutError:
        err = f"stage {stage} timed out after {timeout}s"
        _raw(svc, platform, "stage_timeout", "failed", {"stage": stage, "timeout": timeout}, err)
        _print("TIMEOUT", {"stage": stage, "timeout": timeout})
        return {"ok": False, "stage": stage, "platform": platform, "error": err}
    except Exception as exc:
        _raw(svc, platform, "stage_error", "failed", {"stage": stage}, str(exc))
        _print("ERROR", {"stage": stage, "error": str(exc)})
        return {"ok": False, "stage": stage, "platform": platform, "error": str(exc)}
