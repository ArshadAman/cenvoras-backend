#!/bin/bash
set -e

echo "Applying database migrations..."
python manage.py migrate --noinput

echo "Starting Gunicorn server as multithreaded process manager..."
# Multiplexing: Exactly 2 async workers via Uvicorn to clamp RAM but max CPU
exec gunicorn cenvoras.asgi:application \
    --name cenvoras_web \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --log-level info
