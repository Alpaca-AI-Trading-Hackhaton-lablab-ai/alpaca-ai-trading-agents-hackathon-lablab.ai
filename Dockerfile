# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ALPACA_PAPER_TRADE=true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend.py ./
COPY agents ./agents
COPY services ./services

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=12 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"

CMD ["uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8000"]
