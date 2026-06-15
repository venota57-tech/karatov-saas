import logging

logger = logging.getLogger(__name__)


class Actions:
    """
    Единый слой бизнес-действий
    (сюда подключается WB / Ozon / CRM / FBO)
    """

    async def sync_reviews(self):
        logger.info("SYNC: fetching reviews...")
        # тут позже подключим API маркетплейсов

    async def publish_answer(self, review_id: str, text: str):
        logger.info(f"PUBLISH: answer to {review_id}")
        # публикация ответа

    async def create_listing(self, product: dict):
        logger.info("PUBLISH: new listing")
        # публикация товара

    async def filter_fbo_noise(self, data: dict):
        """
        Убираем 'левак' из live FBO
        """
        logger.info("FBO FILTER: cleaning live data")
        return data
