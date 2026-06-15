FROM python:3.12

WORKDIR /app

# копируем содержимое backend в /app
COPY . /app

# устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# запуск
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
