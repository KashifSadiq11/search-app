#stage 1: Builder
FROM python:3.11-slim AS builder

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
    libgomp1 \
    libpq-dev \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements for caching
COPY services/engine/requirements.txt /tmp/requirements.txt
RUN sed -i "s/psycopg2_binary/psycopg2-binary/g" /tmp/requirements.txt || true

# Install Python dependencies into a dedicated directory
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r /tmp/requirements.txt

# Stage 2: Final runtime image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . /app

WORKDIR /app/services/engine

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host ${APP_HOST} --port ${APP_PORT} --proxy-headers --forwarded-allow-ips=*"]

