# Production Dockerfile for Orpheus Music Service
FROM python:3.11-slim

# Security: Run as non-root user
RUN groupadd -r orpheus && useradd -r -g orpheus orpheus

# Set working directory
WORKDIR /app

# Install system dependencies (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Test/coverage
RUN pip install --no-cache-dir pytest pytest-cov pytest-asyncio

# Copy all Python source (app + tests)
COPY *.py ./

# Copy pre-built seed library (curated MIDI seeds for Orpheus priming)
COPY seed_library/ ./seed_library/

# Persistent cache directory (mounted as volume)
RUN mkdir -p /data/cache && chown -R orpheus:orpheus /data

# Security: Set ownership
RUN chown -R orpheus:orpheus /app

# Security: Switch to non-root user
USER orpheus

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:10002/health || exit 1

# Expose port
EXPOSE 10002

# Run the service
CMD ["python", "-m", "uvicorn", "music_service:app", "--host", "0.0.0.0", "--port", "10002"]
