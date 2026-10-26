# Шаблон Dockerfile для Hugging Face Spaces.
# Перед заливкой подставь свои значения в ENV-строки ниже
# (TELEGRAM_TOKEN, BASE_URL, ADMIN_KEY, WEBHOOK_SECRET) — см. README.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY relay_server.py .

ENV TELEGRAM_TOKEN="" \
    BASE_URL="" \
    ADMIN_KEY="" \
    WEBHOOK_SECRET="" \
    DB_PATH="/app/data/relay.db" \
    PORT=7860

RUN mkdir -p /app/data

CMD ["python", "relay_server.py"]
