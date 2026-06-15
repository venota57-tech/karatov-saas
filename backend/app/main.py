import os
import asyncio
from fastapi import FastAPI
import uvicorn

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "alive"}


# 🚀 ВРЕМЕННО ОТКЛЮЧАЕМ ENGINE ДЛЯ СТАБИЛЬНОГО DEPLOY
@app.on_event("startup")
async def startup_event():
    print("APP STARTED (engine disabled for stability)")
    return


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
