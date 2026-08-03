FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is needed by the container healthcheck; libgl/libglib by PyMuPDF+Pillow.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Named volume mount point. Migrations run automatically at startup, so there is
# no manual step between `docker compose up` and a working system.
ENV DATA_DIR=/data
RUN mkdir -p /data

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "120"]
