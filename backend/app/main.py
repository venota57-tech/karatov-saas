from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import requests
import os

from app.db import SessionLocal, engine
from app.models import Base, Review

app = FastAPI()

Base.metadata.create_all(bind=engine)

WB_API_KEY = os.getenv("WB_API_KEY")
OZON_API_KEY = os.getenv("OZON_API_KEY")
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


# ================= WB =================

@app.post("/sync-wb")
def sync_wb():
    url = "https://feedbacks-api.wildberries.ru/api/v1/feedbacks"

    headers = {
        "Authorization": WB_API_KEY
    }

    params = {
        "isAnswered": False,
        "take": 20,
        "skip": 0
    }

    response = requests.get(url, headers=headers, params=params)
    data = response.json()

    db = SessionLocal()

    for item in data.get("data", {}).get("feedbacks", []):
        text = item.get("text", "")

        exists = db.query(Review).filter(Review.text == text).first()
        if exists:
            continue

        db.add(Review(
            marketplace="wb",
            text=text,
            status="new"
        ))

    db.commit()

    return {"status": "wb synced"}


# ================= OZON =================

@app.post("/sync-ozon")
def sync_ozon():
    url = "https://api-seller.ozon.ru/v1/review/list"

    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY
    }

    payload = {
        "limit": 20,
        "status": "UNPROCESSED"
    }

    response = requests.post(url, headers=headers, json=payload)
    data = response.json()

    db = SessionLocal()

    for item in data.get("reviews", []):
        text = item.get("text", "")

        exists = db.query(Review).filter(Review.text == text).first()
        if exists:
            continue

        db.add(Review(
            marketplace="ozon",
            text=text,
            status="new"
        ))

    db.commit()

    return {"status": "ozon synced"}


# ================= OPENAI =================

@app.post("/generate/{review_id}")
def generate(review_id: int):
    db = SessionLocal()
    review = db.query(Review).get(review_id)

    prompt = f"Ответь на отзыв клиента:\n\n{review.text}"

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
    )

    result = response.json()

    answer = result["choices"][0]["message"]["content"]

    review.answer = answer
    review.status = "done"

    db.commit()

    return {"ok": True}


# ================= ОБЩЕЕ =================

@app.get("/reviews")
def get_reviews():
    db = SessionLocal()
    return db.query(Review).order_by(Review.id.desc()).all()


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
