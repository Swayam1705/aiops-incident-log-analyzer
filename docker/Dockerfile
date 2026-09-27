# syntax=docker/dockerfile:1
# Production-style image: small base, non-root, no secrets, reproducible.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN groupadd --system appgroup && useradd --system --gid appgroup --home /app appuser \
    && mkdir -p /app/logs /app/incidents/generated \
    && chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

# Healthcheck uses the stdlib (no apt-installed curl): version pins break the moment
# python:3.12-slim retargets to a newer Debian, which is what failed CI.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
