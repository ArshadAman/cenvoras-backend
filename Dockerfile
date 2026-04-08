# ---------- Builder Stage ----------
FROM python:3.10-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /install

# Install build dependencies only here
RUN apt-get update && apt-get install -y build-essential

COPY requirements.txt .
RUN pip install --upgrade pip && pip wheel --no-cache-dir --no-deps -r requirements.txt


# ---------- Final Stage ----------
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# Create non-root user
RUN adduser --disabled-password --gecos '' appuser

# Install runtime deps only (if REALLY needed)
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

# Copy wheels and install
COPY --from=builder /install /install
RUN pip install --no-cache-dir /install/*

# Copy app
COPY . /app

# Create logs dir with proper permissions
RUN mkdir -p /app/logs && chown -R appuser:appuser /app

USER appuser

# Use Gunicorn with Uvicorn workers (production-grade ASGI)
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "cenvoras.asgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]