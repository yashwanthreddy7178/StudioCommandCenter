FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY services/common /app/services/common
COPY services/api-gateway /app/services/api-gateway

RUN pip install --no-cache-dir /app/services/api-gateway

WORKDIR /app/services/api-gateway
EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
