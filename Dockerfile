# Stori Composer API - Production Dockerfile
# Multi-stage build for smaller image size
# For reproducible builds, pin base image by digest: python:3.11-slim@sha256:<digest>
# Get digest: docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim

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
COPY --chown=stori:stori scripts/ ./scripts/
COPY --chown=stori:stori alembic/ ./alembic/
COPY --chown=stori:stori alembic.ini ./
COPY --chown=stori:stori pyproject.toml ./
COPY --chown=stori:stori tests/ ./tests/

# Create data directory for SQLite with proper permissions
RUN mkdir -p /data && chown -R stori:stori /data && chmod 755 /data

# Switch to non-root user
USER stori

# Ensure data directory is writable
VOLUME ["/data"]

# Environment defaults (PYTHONPATH so alembic/scripts find app from any CWD)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    STORI_DEBUG=false \
    STORI_HOST=0.0.0.0 \
    STORI_PORT=10001 \
    STORI_DATABASE_URL=sqlite+aiosqlite:////data/stori.db

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:10001/api/v1/health')"

EXPOSE 10001

# Run with uvicorn
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10001"]
