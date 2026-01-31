# ------------------------------
# Stage 1: Builder (install deps)
# ------------------------------
FROM python:3.11-slim AS builder

# Python environment settings
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    git \
    wget \
    curl \
    libpq-dev \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first for caching
COPY services/engine/requirements.txt /tmp/requirements.txt

# Fix psycopg2 naming if needed
RUN sed -i "s/psycopg2_binary/psycopg2-binary/g" /tmp/requirements.txt || true

# Install Python dependencies into /install
RUN pip install --upgrade pip && \
    pip install --prefix=/install --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt

# ------------------------------
# Stage 2: Runtime image
# ------------------------------
FROM python:3.11-slim

# Python environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . /app

# Copy .env for environment variables
COPY .env /app/.env

# Set working directory where main.py lives
WORKDIR /app/services/engine

# Expose FastAPI port
EXPOSE 8000

# Start FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
