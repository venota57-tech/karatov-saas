import os
import json
import redis
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.engine import CoreEngine

app = FastAPI()

# =========================
# ENGINE START
# =========================
engine = CoreEngine()


@app.on_event("startup")
async def startup():
    import asyncio
    asyncio.create_task(engine.start())


# =========================
# API: TASK QUEUE
# =========================
@app.post("/tasks")
def create_task(task: dict):
    r = redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
    r.rpush("tasks", json.dumps(task))
    return {"status": "queued"}


# =========================
# HEALTH
# =========================
@app.get("/")
def root():
    return {"status": "ok", "service": "karatov-saas"}


@app.get("/health")
def health():
    return {"status": "alive"}


# =========================
# FRONTEND (DASHBOARD)
# =========================
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
