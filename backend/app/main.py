import os
import asyncio

from fastapi import FastAPI
import uvicorn

from app.engine.runner import start_engine

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok", "engine": "controlled"}


@app.get("/health")
def health():
    return {"status": "alive"}


# =========================
# SAFE STARTUP (Render-friendly)
# =========================
@app.on_event("startup")
async def startup():
    # engine запускается БЕЗ блокировки FastAPI
    asyncio.create_task(start_engine())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port
    )
