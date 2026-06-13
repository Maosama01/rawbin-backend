# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: builder — install Python dependencies into a clean venv
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed to compile certain wheels (psycopg2-binary ships its own
# libpq, but asyncpg needs gcc for cython extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: runtime — lean final image
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="Newcycl Clean Technologies <dev@newcycl.com>"
LABEL org.opencontainers.image.title="rawbin-backend"
LABEL org.opencontainers.image.description="Rawbin Companion App Backend API"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/install/bin:$PATH"

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Create a non-root user to run the app
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy application source
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8000

# Default: start the API server.
# Override CMD in docker-compose.yml for the Celery worker.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "app/core/log_config.json"]
