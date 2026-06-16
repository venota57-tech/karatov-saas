# ---------- FRONTEND BUILD ----------
FROM node:18 AS frontend-build

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---------- BACKEND ----------
FROM python:3.12

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=frontend-build /frontend/dist /app/frontend/dist

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
