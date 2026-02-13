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

# Copy application code
COPY music_service.py generation_policy.py quality_metrics.py ./

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
