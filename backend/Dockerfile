FROM python:3.12

# рабочая директория внутри контейнера
WORKDIR /app

# копируем ВЕСЬ проект (и backend, и frontend)
COPY . .

# переходим в backend
WORKDIR /app/backend

# устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# запускаем сервер
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
