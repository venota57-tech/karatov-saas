#!/bin/bash

PROJECT="karatov-level3"

echo "Creating project..."

mkdir -p $PROJECT
cd $PROJECT

# ---------------- ROOT ----------------
mkdir -p backend frontend data/postgres

# ---------------- DOCKER COMPOSE ----------------
cat << 'YAML' > docker-compose.yml
version: "3.9"

services:
  db:
    image: postgres:15
    environment:
      POSTGRES_USER: karatov
      POSTGRES_PASSWORD: karatov
      POSTGRES_DB: karatov
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7

  backend:
    image: python:3.12-slim
    working_dir: /app
    volumes:
      - ./backend:/app
    command: bash -c "pip install fastapi uvicorn && uvicorn main:app --host 0.0.0.0 --port 8000"
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis

  worker:
    image: python:3.12-slim
    working_dir: /app
    volumes:
      - ./backend:/app
    command: python worker.py
    depends_on:
      - redis

  frontend:
    image: node:20-alpine
    working_dir: /app
    volumes:
      - ./frontend:/app
    command: sh -c "npm install && npm run dev -- --host"
    ports:
      - "5173:5173"
YAML

# ---------------- ENV ----------------
cat << 'ENV' > .env
OPENAI_API_KEY=put_key_here
OPENAI_MODEL=gpt-5.4-mini

WB_TOKEN=put_key_here
OZON_CLIENT_ID=put_key_here
OZON_API_KEY=put_key_here

REDIS_URL=redis://redis:6379

AUTO_PUBLISH=true
IDEMPOTENCY=true
ENV

# ---------------- BACKEND ----------------
cat << 'PY' > backend/main.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"status": "LEVEL 3 READY"}
PY

cat << 'PY' > backend/worker.py
import time

while True:
    print("worker alive")
    time.sleep(5)
PY

# ---------------- FRONTEND ----------------
cat << 'JSON' > frontend/package.json
{
  "name": "frontend",
  "private": true,
  "scripts": {
    "dev": "vite"
  }
}
JSON

cat << 'HTML' > frontend/index.html
<!doctype html>
<html>
  <body>
    <div>LEVEL 3 FRONTEND READY</div>
  </body>
</html>
HTML

echo "DONE."
echo "Run:"
echo "cd $PROJECT && docker compose up --build"
