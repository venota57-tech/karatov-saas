FROM python:3.12

WORKDIR /app

# копируем ТОЛЬКО backend
COPY backend /app/backend

# копируем frontend отдельно
COPY frontend /app/frontend

# идём в backend
WORKDIR /app/backend

# устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# запуск
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
