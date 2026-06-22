from __future__ import annotations

import json
import time

from redis import Redis

from app.config import settings
from app.queue_service import QUEUE_NAME, ensure_job_tables
from app.worker_tasks import run_sync_job


def main() -> None:
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is not configured")
    ensure_job_tables()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    print(f"[worker] started; queue={QUEUE_NAME}")
    while True:
        item = redis.brpop(QUEUE_NAME, timeout=5)
        if not item:
            continue
        _, raw = item
        try:
            msg = json.loads(raw)
            print("[worker] job received", msg)
            run_sync_job(sync_job_id=int(msg["sync_job_id"]), job_type=msg["job_type"], platform=msg.get("platform"), block=msg.get("block"), payload=msg.get("payload") or {})
            print("[worker] job completed", msg.get("sync_job_id"))
        except Exception as exc:
            print("[worker] job failed:", exc)
            time.sleep(1)


if __name__ == "__main__":
    main()
