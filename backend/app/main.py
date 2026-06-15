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


# 🔥 теперь GET чтобы можно было дергать из браузера
@app.get("/sync-wb")
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

    if response.status_code != 200:
        return {"error": response.text}

    data = response.json()

    db = SessionLocal()

    added = 0

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
        added += 1

    db.commit()

    return {"status": "ok", "added": added}


@app.get("/reviews")
def get_reviews():
    db = SessionLocal()
    return db.query(Review).order_by(Review.id.desc()).all()


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

    review.answer = result["choices"][0]["message"]["content"]
    review.status = "done"

    db.commit()

    return {"ok": True}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
