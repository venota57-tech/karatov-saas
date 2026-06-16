FROM python:3.12

WORKDIR /app

COPY . /app

# backend зависимости
RUN pip install --no-cache-dir -r requirements.txt

# установка node
RUN apt-get update && apt-get install -y nodejs npm

# сборка frontend
WORKDIR /app/backend/frontend
RUN npm install
RUN npm run build

# обратно в backend
WORKDIR /app/backend

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]