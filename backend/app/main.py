from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.db import SessionLocal, engine
from app.models import Base, Review

app = FastAPI()

# создаём таблицы
Base.metadata.create_all(bind=engine)

@app.get("/reviews")
def get_reviews():
    db = SessionLocal()
    return db.query(Review).all()

@app.post("/reviews")
def create_review(text: str):
    db = SessionLocal()
    review = Review(text=text, marketplace="wb")
    db.add(review)
    db.commit()
    return {"status": "ok"}

@app.post("/generate/{review_id}")
def generate(review_id: int):
    db = SessionLocal()
    review = db.query(Review).get(review_id)

    review.answer = "Тестовый ответ"
    review.status = "done"

    db.commit()
    return {"ok": True}

# ВАЖНО: фронт подключаем ВМЕСТО root endpoint
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")def root():
    return {"status": "ok", "service": "karatov-saas"}


@app.get("/health")
def health():
    return {"status": "alive"}


# =========================
# FRONTEND (DASHBOARD)
# =========================
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
