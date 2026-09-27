# syntax=docker/dockerfile:1
# Production-style image: small base, non-root, no secrets, reproducible.
FROM python:3.12-slim AS base

# Deterministic, no .pyc clutter, unbuffered logs for clean structured logging.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is used only for the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl=7.88.1-10+deb12u* \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching / reproducibility.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source (no secrets, no model weights, no caches).
COPY app ./app
COPY scripts ./scripts

# Non-root runtime user.
RUN groupadd --system appgroup && useradd --system --gid appgroup --home /app appuser \
    && mkdir -p /app/logs /app/incidents/generated \
    && chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

# Application-defined probes are the source of truth in Kubernetes; this HEALTHCHECK
# mirrors the readiness probe for plain Docker/compose runs.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1

# Sensible startup: bind 0.0.0.0 so the container is reachable in kind/compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
