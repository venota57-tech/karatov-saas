import os
import time
import json
import asyncio
import redis

REDIS_URL = os.getenv("REDIS_URL")

r = redis.from_url(REDIS_URL, decode_responses=True)


async def process_task(task):
    """
    Здесь будет логика:
    - WB / Ozon API
    - генерация ответа
    - публикация
    """

    print("🔥 processing task:", task)

    # TODO: подключим реальные marketplace вызовы
    await asyncio.sleep(1)


async def engine_loop():
    print("🚀 ENGINE STARTED")

    while True:
        try:
            task = r.lpop("engine_queue")

            if task:
                task_data = json.loads(task)
                await process_task(task_data)
            else:
                await asyncio.sleep(5)

        except Exception as e:
            print("❌ ENGINE ERROR:", str(e))
            await asyncio.sleep(5)
