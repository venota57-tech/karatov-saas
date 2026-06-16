from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
import os

# твои реальные модули
from app.ai.answer_generator import AnswerGenerator
from app.services.autopublish_service import autopublish_reviews
from app.marketplace_clients.wb import WBClient  # поправил импорт

app = FastAPI()

# ===== ИНИЦИАЛИЗАЦИЯ =====

generator = AnswerGenerator()
wb = WBClient()

# ===== РЕАЛЬНЫЕ ОТЗЫВЫ =====

@app.get("/reviews")
def get_reviews():
    try:
        reviews = wb.get_reviews()
        return reviews

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


# ===== ГЕНЕРАЦИЯ =====

@app.post("/generate")
async def generate_answer(req: Request):
    data = await req.json()

    try:
        result = generator.generate_for_review(data)
        return result

    except Exception as e:
        return {"error": str(e)}


# ===== АВТОПАБЛИШ =====

@app.post("/autopublish")
async def autopublish():
    try:
        result = autopublish_reviews()
        return result

    except Exception as e:
        return {"error": str(e)}


# ===== FRONTEND (REACT) =====

frontend_path = os.path.join(os.path.dirname(__file__), "../frontend/dist")

# assets (vite)
app.mount(
    "/assets",
    StaticFiles(directory=os.path.join(frontend_path, "assets")),
    name="assets"
)

# главная страница
@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join(frontend_path, "index.html"))