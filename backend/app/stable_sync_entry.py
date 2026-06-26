from __future__ import annotations

import asyncio
import os

from app.database import SessionLocal
from app.services.stable_marketplace_os import StableMarketplaceOS


async def main():
    kind = (os.getenv("GITHUB_SYNC_KIND") or "customer_ops").lower()
    platform = (os.getenv("GITHUB_SYNC_PLATFORM") or "ALL").upper()
    db = SessionLocal()
    try:
        svc = StableMarketplaceOS(db)
        if kind == "operations":
            result = await svc.sync_operations(platform)
        elif kind == "customer_ops":
            result = await svc.sync_customer_ops(platform)
        else:
            # Delegate legacy blocks to the existing runner when available.
            import runpy
            os.environ["GITHUB_SYNC_KIND"] = kind
            runpy.run_module("app.github_sync_runner", run_name="__main__")
            return
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
