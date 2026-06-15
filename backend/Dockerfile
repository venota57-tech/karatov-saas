FROM python:3.12

WORKDIR /app

# копируем весь проект
COPY . .

# идём в backend
WORKDIR /app/backend

RUN pip install --no-cache-dir -r requirements.txt

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
