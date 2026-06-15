import logging
import time

logger = logging.getLogger(__name__)


class Observer:
    """
    LIVE FEEDBACK LAYER
    показывает что система реально работает
    """

    def event(self, name: str, data=None):
        logger.info(f"[EVENT] {name} | {data}")

    def heartbeat(self):
        logger.info(f"[HEARTBEAT] system alive | {time.time()}")
