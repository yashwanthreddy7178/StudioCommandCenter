FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY services/common /app/services/common
COPY services/mcp-gateway /app/services/mcp-gateway

RUN pip install --no-cache-dir /app/services/mcp-gateway

WORKDIR /app/services/mcp-gateway
EXPOSE 8001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
