#!/bin/bash
set -e

echo "Applying database migrations..."
python manage.py migrate --noinput

# Start persistent Warm Chromium CDP Daemon on port 9222 for sub-150ms vector PDF rendering
CHROME_BIN=$(which chromium || which chromium-browser || which google-chrome || echo "")
if [ -n "$CHROME_BIN" ]; then
    echo "Starting Warm Chromium CDP Daemon on port 9222 using $CHROME_BIN..."
    mkdir -p /tmp/chromium-daemon-data
    $CHROME_BIN \
        --headless=new \
        --remote-debugging-port=9222 \
        --remote-debugging-address=127.0.0.1 \
        --disable-gpu \
        --no-sandbox \
        --disable-dev-shm-usage \
        --disable-software-rasterizer \
        --no-first-run \
        --no-default-browser-check \
        --user-data-dir=/tmp/chromium-daemon-data &
fi

echo "Starting Gunicorn server as multithreaded process manager..."
# Multiplexing: Exactly 2 async workers via Uvicorn to clamp RAM but max CPU
exec gunicorn cenvoras.asgi:application \
    --name cenvoras_web \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --log-level info
