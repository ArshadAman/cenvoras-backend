#!/bin/bash
set -e

echo "Applying database migrations..."
python manage.py migrate --noinput

# Start persistent Warm Chromium CDP Daemon on port 9222 for sub-150ms vector PDF rendering
# Hyper-optimized for 1 vCPU / 2 GB RAM (Single-Process, 96MB V8 JS Heap cap, stripped subsystems)
CHROME_BIN=$(which chromium || which chromium-browser || which google-chrome || echo "")
if [ -n "$CHROME_BIN" ]; then
    echo "Starting Warm Chromium CDP Daemon on port 9222 (Single-Process, Capped Memory)..."
    rm -rf /tmp/chromium-daemon-data
    mkdir -p /tmp/chromium-daemon-data
    nice -n 10 $CHROME_BIN \
        --headless=new \
        --remote-debugging-port=9222 \
        --remote-debugging-address=127.0.0.1 \
        --single-process \
        --no-sandbox \
        --disable-gpu \
        --disable-dev-shm-usage \
        --disable-software-rasterizer \
        --disable-background-networking \
        --disable-default-apps \
        --disable-extensions \
        --disable-sync \
        --disable-translate \
        --mute-audio \
        --hide-scrollbars \
        --renderer-process-limit=1 \
        --js-flags="--max-old-space-size=96" \
        --no-first-run \
        --no-default-browser-check \
        --user-data-dir=/tmp/chromium-daemon-data &
fi

echo "Starting Gunicorn server as multithreaded process manager..."
# Multiplexing: 2 async workers via Uvicorn with auto-recycling to eliminate Python memory fragmentation
exec gunicorn cenvoras.asgi:application \
    --name cenvoras_web \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --timeout 60 \
    --keep-alive 5 \
    --log-level info
