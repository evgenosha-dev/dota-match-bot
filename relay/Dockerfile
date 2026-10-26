# Hugging Face Spaces (Docker SDK).
# Секреты (TELEGRAM_TOKEN, BASE_URL, ADMIN_KEY, WEBHOOK_SECRET) задаются
# в настройках Space → Secrets and variables → Space secrets — НЕ вписывай
# их сюда: репозиторий Space публичный, и токен увидит кто угодно.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY relay_server.py .

ENV DB_PATH="/app/data/relay.db" \
    PORT=7860

RUN mkdir -p /app/data

CMD ["python", "relay_server.py"]
