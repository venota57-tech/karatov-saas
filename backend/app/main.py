import os
import asyncio

from fastapi import FastAPI
import uvicorn

# импорт engine
from app.engine.engine import engine_loop

app = FastAPI()


# =========================
# HEALTH CHECK (ВАЖНО ДЛЯ RENDER)
# =========================
@app.get("/")
def root():
    return {"status": "ok", "service": "karatov-saas"}


@app.get("/health")
def health():
    return {"status": "healthy"}


# =========================
# ENGINE START (IN-PROCESS CORE)
# =========================
@app.on_event("startup")
async def startup_event():
    """
    Запускаем background engine БЕЗ блокировки сервера
    """
    asyncio.create_task(engine_loop())


# =========================
# MAIN ENTRY (LOCAL + RENDER SAFE)
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port
    )
