import asyncio
import logging

from app.engine.engine import engine_loop

logger = logging.getLogger(__name__)

_engine_task = None


async def start_engine():
    """
    Safe engine starter for production (Render-safe)
    """
    global _engine_task

    if _engine_task and not _engine_task.done():
        logger.info("Engine already running")
        return

    logger.info("Starting engine in background mode...")

    _engine_task = asyncio.create_task(
        engine_loop_wrapper(),
        name="engine_task"
    )


async def engine_loop_wrapper():
    """
    Wrapper that protects FastAPI from engine crashes
    """
    try:
        await engine_loop()
    except Exception as e:
        logger.exception(f"Engine crashed: {e}")
        await asyncio.sleep(5)
        logger.info("Restarting engine...")
        await engine_loop_wrapper()
