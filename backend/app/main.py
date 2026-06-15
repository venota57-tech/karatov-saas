import os
import asyncio
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "alive"}


# IMPORTANT: Render requires app object ONLY
# DO NOT block startup, DO NOT run uvicorn here


@app.on_event("startup")
async def startup():
    print("APP STARTED - PORT SHOULD BE ACTIVE")
