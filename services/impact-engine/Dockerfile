FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY services/common /app/services/common
COPY services/impact-engine /app/services/impact-engine

RUN pip install --no-cache-dir /app/services/impact-engine

WORKDIR /app/services/impact-engine
EXPOSE 8002

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8002"]
