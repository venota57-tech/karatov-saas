import os
from fastapi import FastAPI
import asyncio

from app.core.engine import CoreEngine

app = FastAPI()

engine = CoreEngine()


@app.get("/")
def root():
    return {"status": "ok", "core": "active"}


@app.get("/health")
def health():
    return {"status": "alive"}


@app.on_event("startup")
async def startup():
    asyncio.create_task(engine.start())
