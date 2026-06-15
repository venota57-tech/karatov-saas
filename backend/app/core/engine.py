import asyncio
import logging
import os
import json
import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL")
r = redis.from_url(REDIS_URL, decode_responses=True)


class CoreEngine:
    def __init__(self):
        self.running = False
        self.task = None

    async def start(self):
        if self.running:
            return

        self.running = True
        logger.info("CORE ENGINE STARTED")

        self.task = asyncio.create_task(self.loop())

    async def loop(self):
        while self.running:
            try:
                await self.process_queue()
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Engine error: {e}")
                await asyncio.sleep(3)

    async def process_queue(self):
        task = r.lpop("tasks")

        if not task:
            return

        data = json.loads(task)

        logger.info(f"Processing task: {data}")

        await self.handle_task(data)

    async def handle_task(self, task):
        """
        ЕДИНАЯ ТОЧКА БИЗНЕС-ЛОГИКИ
        """

        task_type = task.get("type")

        # -------------------------
        # 1. AUTOSYNC REVIEWS
        # -------------------------
        if task_type == "sync_reviews":
            await self.sync_reviews(task)

        # -------------------------
        # 2. AUTO PUBLISH ANSWERS
        # -------------------------
        elif task_type == "publish_answer":
            await self.publish_answer(task)

        # -------------------------
        # 3. FBO FILTER (УБИРАЕМ "ЛЕВАК")
        # -------------------------
        elif task_type == "fbo_filter":
            await self.fbo_filter(task)

        else:
            logger.warning(f"Unknown task: {task_type}")

    async def sync_reviews(self, task):
        logger.info("Syncing reviews...")

    async def publish_answer(self, task):
        logger.info("Publishing answer...")

    async def fbo_filter(self, task):
        logger.info("Filtering FBO data (removing noise)...")
