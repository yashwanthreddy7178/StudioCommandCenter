FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY services/common /app/services/common
COPY services/action-executor /app/services/action-executor

RUN pip install --no-cache-dir /app/services/action-executor

WORKDIR /app/services/action-executor
EXPOSE 8003

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8003"]
