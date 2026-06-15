import asyncio
import logging
import json
import redis
import os

from app.core.actions import Actions

logger = logging.getLogger(__name__)

r = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)


class CoreEngine:
    """
    SaaS Core Engine v2 (FINAL CLEAN VERSION)
    - routing
    - queue processing
    - business actions layer
    - safe Render execution
    """

    def __init__(self):
        self.running = False
        self.task = None
        self.actions = Actions()

    async def start(self):
        if self.running:
            return

        self.running = True
        logger.info("CORE ENGINE v2 STARTED")

        self.task = asyncio.create_task(self.loop())

    async def loop(self):
        while self.running:
            try:
                await self.tick()
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Engine crash handled: {e}")
                await asyncio.sleep(3)

    async def tick(self):
        raw = r.lpop("tasks")

        if not raw:
            return

        task = json.loads(raw)

        logger.info(f"[CORE] task received: {task}")

        await self.router(task)

    # =========================
    # ROUTER
    # =========================
    async def router(self, task: dict):
        task_type = task.get("type")

        if task_type == "review.ingest":
            await self.actions.sync_reviews()

        elif task_type == "review.reply":
            await self.actions.publish_answer(
                task.get("review_id"),
                task.get("text")
            )

        elif task_type == "publish.auto":
            await self.actions.create_listing(task.get("product"))

        elif task_type == "fbo.filter":
            await self.actions.filter_fbo_noise(task)

        else:
            logger.warning(f"Unknown task type: {task_type}")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
