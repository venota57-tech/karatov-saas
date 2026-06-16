FROM python:3.12

WORKDIR /app

# копируем backend отдельно
COPY backend /app/backend

# backend зависимости
WORKDIR /app/backend
RUN pip install --no-cache-dir -r requirements.txt

# установка node
RUN apt-get update && apt-get install -y nodejs npm

# фронт
WORKDIR /app/backend/frontend
RUN ls -la
RUN npm install
RUN npm run build

# запуск
WORKDIR /app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]