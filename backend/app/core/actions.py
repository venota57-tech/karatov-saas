import logging

logger = logging.getLogger(__name__)


class Actions:
    """
    Business Logic Layer (SaaS)
    WB / Ozon / FBO / Reviews
    """

    async def sync_reviews(self):
        logger.info("ACTION: syncing reviews")

    async def publish_answer(self, review_id: str, text: str):
        logger.info(f"ACTION: publishing answer for {review_id}")

    async def create_listing(self, product: dict):
        logger.info("ACTION: publishing product listing")

    async def filter_fbo_noise(self, data: dict):
        logger.info("ACTION: filtering FBO live noise")
        return data
