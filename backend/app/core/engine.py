import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CoreEngine:
    """
    Единый управляющий engine (SaaS core)
    Без блокировок, без дублей, safe for Render
    """

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """
        Запуск core engine (idempotent)
        """
        if self._running:
            logger.info("CoreEngine already running")
            return

        self._running = True
        logger.info("CoreEngine starting...")

        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self):
        """
        Главный цикл обработки задач
        """
        try:
            while self._running:
                await self.tick()
                await asyncio.sleep(2)  # control interval
        except Exception as e:
            logger.exception(f"CoreEngine crashed: {e}")
            self._running = False

            # auto-recovery
            await asyncio.sleep(5)
            await self.start()

    async def tick(self):
        """
        ОДИН цикл обработки:
        - sync WB
        - sync Ozon
        - FBO filtering
        - queue processing
        """

        # TODO: сюда подключаем бизнес-логику

        logger.info("CoreEngine tick executed")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
