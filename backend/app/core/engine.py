import asyncio
import logging
import json
import redis
import os

logger = logging.getLogger(__name__)

r = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)


class CoreEngine:
    """
    SaaS Business Core Engine v2
    - routing
    - queue processing
    - business logic separation
    """

    def __init__(self):
        self.running = False
        self.task = None

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
    # ROUTER LAYER (ключевой слой)
    # =========================
    async def router(self, task: dict):
        t = task.get("type")

        if t == "review.ingest":
            await self.handle_review(task)

        elif t == "review.reply":
            await self.handle_reply(task)

        elif t == "publish.auto":
            await self.handle_publish(task)

        elif t == "fbo.filter":
            await self.handle_fbo_filter(task)

        else:
            logger.warning(f"Unknown task type: {t}")

    # =========================
    # BUSINESS WORKERS
    # =========================

    async def handle_review(self, task):
        """
        получение отзывов
        """
        logger.info("Processing review ingestion")

    async def handle_reply(self, task):
        """
        автоответы на отзывы
        """
        logger.info("Generating reply")

    async def handle_publish(self, task):
        """
        публикация контента
        """
        logger.info("Publishing content")

    async def handle_fbo_filter(self, task):
        """
        УБИРАЕМ 'ЛЕВАК' И МУСОР ИЗ LIVE FBO
        """
        logger.info("Filtering FBO noise data")
