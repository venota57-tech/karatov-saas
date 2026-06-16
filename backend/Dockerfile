FROM python:3.12

WORKDIR /app

# копируем всё (мы уже внутри backend)
COPY . /app

# зависимости backend
RUN pip install --no-cache-dir -r requirements.txt

# ставим node
RUN apt-get update && apt-get install -y nodejs npm

# собираем фронт
WORKDIR /app/frontend
RUN npm install
RUN npm run build

# назад
WORKDIR /app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]