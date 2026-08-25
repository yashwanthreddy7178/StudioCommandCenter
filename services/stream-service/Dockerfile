FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY services/common /app/services/common
COPY services/stream-service /app/services/stream-service

RUN pip install --no-cache-dir /app/services/stream-service

WORKDIR /app/services/stream-service
EXPOSE 8005

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8005"]
