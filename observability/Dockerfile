FROM python:3.11-slim AS base

# Set environment to avoid .pyc and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System build dependencies (cleaned up later)
# libgl1 / libglib2.0 needed for Pillow / torchvision, build-essential for some libs, wget for potential model downloads
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

# Copy only requirements first for better caching
COPY services/engine/requirements.txt /tmp/requirements.txt

# Fix known package name typo in requirements (if present)
RUN sed -i "s/psycopg2_binary/psycopg2-binary/g" /tmp/requirements.txt || true

# Install Python dependencies
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

# Copy application code (avoid copying local venv, caches via .dockerignore)
COPY . /app

# Expose FastAPI port
EXPOSE 8000

# Set working directory to engine where main:app resides
WORKDIR /app/services/engine

# Run the app relying entirely on environment variables provided at runtime (.env via compose)
CMD ["sh", "-c", "uvicorn main:app --host ${APP_HOST} --port ${APP_PORT} --proxy-headers --forwarded-allow-ips=*"]
