# Multi-stage Dockerfile for production-grade deployment
# Stage 1: Builder - Install dependencies and compile packages
FROM python:3.11-slim-bookworm AS builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime - Minimal production image
FROM python:3.11-slim-bookworm

# Set labels for image metadata
LABEL maintainer="FinGuru Team"
LABEL version="3.0.0"
LABEL description="FinGuru -- financial API with token identity, RLS tenancy and AA connectivity"

# Set working directory
WORKDIR /app

# Set environment variables for production
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH" \
    # Production settings
    DEBUG=False \
    LOG_LEVEL=INFO

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create non-root user for security
RUN useradd -m -u 1000 finguru && \
    mkdir -p /app/data/chromadb /app/logs && \
    chown -R finguru:finguru /app

# Copy application code. .dockerignore keeps .env, .git, data/ and the test
# suite out of the image -- a container that ships a .env ships its secrets.
COPY --chown=finguru:finguru . .

# Switch to non-root user
USER finguru

# Expose port
EXPOSE 8000

# Health check with proper configuration
HEALTHCHECK --interval=30s \
            --timeout=10s \
            --start-period=40s \
            --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Use exec form for proper signal handling
CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--access-log", \
     "--no-use-colors"]
