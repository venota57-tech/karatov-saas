from __future__ import annotations

import asyncio
import os

from app.database import SessionLocal
from app.services.recovery_v5 import RecoveryV5
from app.services.recovery_v5_stages import run_stage


async def main():
    stage = (
        os.getenv("RECOVERY_V5_STAGE")
        or os.getenv("RECOVERY_V5_KIND")
        or os.getenv("GITHUB_SYNC_KIND")
        or "all_safe"
    )
    platform = (os.getenv("RECOVERY_V5_PLATFORM") or os.getenv("GITHUB_SYNC_PLATFORM") or "ALL").upper()
    deep = (os.getenv("RECOVERY_V5_DEEP") or "").lower() in {"1", "true", "yes", "deep", "nightly"}

    db = SessionLocal()
    try:
        svc = RecoveryV5(db)
        result = await run_stage(svc, stage=stage, platform=platform, deep=deep)
        print(result, flush=True)
        if not result.get("ok", False):
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
