from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Karatov</title>
        <style>
            body { background: #111; color: white; font-family: Arial; padding: 20px; }
            button { padding: 10px; background: green; color: white; border: none; }
        </style>
    </head>
    <body>
        <h1>Отзывы</h1>
        <button onclick="load()">Обновить</button>
        <div id="reviews"></div>

        <script>
        async function load() {
            const r = await fetch('/reviews');
            const data = await r.json();
            const el = document.getElementById('reviews');
            el.innerHTML = data.map(x => '<div>'+x.text+'</div>').join('');
        }
        </script>
    </body>
    </html>
    """

@app.get("/reviews")
def reviews():
    return [
        {"text": "Отличный товар"},
        {"text": "Быстрая доставка"},
        {"text": "Плохое качество"}
    ]