# Maestro — Production Dockerfile
# Multi-stage build: builder installs deps into wheels; runtime copies only the wheels.
#
# Layer invalidation guide (when to rebuild):
#   requirements.txt changed  →  docker compose build maestro
#   Python code changed       →  no rebuild (override.yml bind-mounts app/ tests/ etc.)
#   New tool/script added     →  docker compose build maestro (or add to override mounts)
#
# Pin base image for reproducible production builds:
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim

FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt


FROM python:3.11-slim as runtime
# Pin by digest in production; see comment at top of Dockerfile.

WORKDIR /app

# Create non-root user for security
RUN groupadd -r stori && useradd -r -g stori stori

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels and install
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir /wheels/*

# Test/coverage (install with deps so coverage is available for pytest -v + coverage report)
RUN pip install --no-cache-dir pytest-cov

# Copy application code
COPY --chown=stori:stori app/ ./app/
COPY --chown=stori:stori tests/ ./tests/
COPY --chown=stori:stori scripts/ ./scripts/
COPY --chown=stori:stori tools/ ./tools/
COPY --chown=stori:stori alembic/ ./alembic/
COPY --chown=stori:stori stori_tourdeforce/ ./stori_tourdeforce/
COPY --chown=stori:stori alembic.ini pyproject.toml ./
COPY --chown=stori:stori scripts/e2e/mvp_happy_path.py ./
COPY --chown=stori:stori entrypoint.sh ./

# Create data directory for SQLite with proper permissions
RUN mkdir -p /data && chown -R stori:stori /data && chmod 755 /data

# Switch to non-root user
USER stori

# Ensure data directory is writable
VOLUME ["/data"]

# Infrastructure env vars (PYTHONPATH so alembic/scripts find app from any CWD).
# Application defaults (DEBUG, HOST, PORT, DATABASE_URL, etc.) live in app/config.py
# and are overridden via .env / docker-compose environment: blocks — not here.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:10001/api/v1/health')"

EXPOSE 10001

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10001"]
