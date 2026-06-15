import os
import asyncio
from fastapi import FastAPI

from app.core.engine import CoreEngine

app = FastAPI()

engine = CoreEngine()


@app.get("/")
def root():
    return {"status": "ok", "system": "saas-core-v1"}


@app.get("/health")
def health():
    return {"status": "alive"}


@app.on_event("startup")
async def startup():
    asyncio.create_task(engine.start())
