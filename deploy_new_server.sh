#!/bin/bash
# ==============================================================================
# Cenvora One-Click Deployment & Bootstrap Script for New Server
# Domain: newapi.cenvora.app
# Usage:
#   chmod +x deploy_new_server.sh
#   ./deploy_new_server.sh
# ==============================================================================

set -e

DOMAIN="newapi.cenvora.app"
EMAIL="support@cenvora.app"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "  🚀 CENVORA ONE-CLICK DEPLOYMENT: ${DOMAIN}"
echo "=================================================="

# Detect Docker command and socket permission
DOCKER_CMD="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  if command -v docker-compose >/dev/null 2>&1; then
    DOCKER_CMD="docker-compose"
  else
    echo "⚠️  Docker / Docker Compose not detected. Installing Docker..."
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sudo sh /tmp/get-docker.sh
    sudo usermod -aG docker "$USER" || true
    sudo systemctl enable --now docker
    DOCKER_CMD="docker compose"
  fi
fi

# If user doesn't have permission to docker.sock yet without re-logging in, use sudo
if ! $DOCKER_CMD ps >/dev/null 2>&1; then
  if sudo $DOCKER_CMD ps >/dev/null 2>&1; then
    DOCKER_CMD="sudo $DOCKER_CMD"
  fi
fi

echo "✓ Using Docker engine: $DOCKER_CMD"

# 1. Environment File Check / Creation
echo ""
echo "--- [1/6] Checking .env configuration ---"
if [ ! -f .env ]; then
  echo "Generating production .env file..."
  cat > .env <<'EOF'
POSTGRES_DB=cenvoras_db
POSTGRES_USER=cenvoras_user
POSTGRES_PASSWORD=cenvoras_password
POSTGRES_HOST=db
POSTGRES_PORT=5432
DJANGO_SECRET_KEY=m!6d*i$$s8v$$4a244!%u040+cz-fktle@o8yus&@xoui_vso9@!
DEBUG=False
USE_SQLITE=False
USE_LOCAL_CACHE=False
DJANGO_ALLOWED_HOSTS=newapi.cenvora.app,api.cenvora.app,devapi.cenvora.app,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://newapi.cenvora.app,https://api.cenvora.app,https://devapi.cenvora.app
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://cenvora.app,https://www.cenvora.app,https://dev.cenvora.app,https://newapi.cenvora.app,https://api.cenvora.app
SECURE_HSTS_SECONDS=31536000
POSTGRES_CONNECT_TIMEOUT=10
POSTGRES_SSLMODE=prefer
GEMINI_API_KEY="AIzaSyBQYGknXF5sUQZy24DaCGBKBzsjBnkRggw"
CLOUDINARY_CLOUD_NAME="da7rhb6xj"
CLOUDINARY_API_KEY="293216685981532"
CLOUDINARY_API_SECRET="0ZbCC7UxoUbfwgqTN-Vl9p54w64"
TRANSACTIONAL_EMAIL_API_KEY="U2ZFOP7jn8QL0irz0xnrHa2jmp0TljDavuySOve7TphWefLC01cJMuEQdmXXeiPz"
TRANSACTIONAL_EMAIL_SENDER_EMAIL=noreply@email.cenvora.app
TRANSACTIONAL_EMAIL_SENDER_NAME="Cenvora Cloud"
TRANSACTIONAL_EMAIL_API_URL="https://api.ahasend.com/v1"
BACKUP_ALERT_EMAIL=support@cenvora.app
DOZZLE_USERNAME=admin
DOZZLE_PASSWORD=cenvoras_logs
GOOGLE_CLIENT_ID=1072296580062-cvlumiglcsg77jqbal1f727vkhvkats1.apps.googleusercontent.com
EOF
  echo "✓ Created .env"
else
  echo "✓ Existing .env file found."
fi

# 2. Free up ports 80, 443, 6379, 5432 if used by host services
echo ""
echo "--- [2/6] Freeing host ports (80, 443, 6379, 5432) ---"
for svc in nginx apache2 caddy redis redis-server postgresql; do
  if systemctl is-active --quiet "$svc" 2>/dev/null; then
    echo "Stopping host service: $svc"
    sudo systemctl stop "$svc" || true
    sudo systemctl disable "$svc" || true
  fi
done

# Kill any process holding 80, 443, 6379, 5432 if still running on host
if command -v fuser >/dev/null 2>&1; then
  sudo fuser -k 80/tcp 2>/dev/null || true
  sudo fuser -k 443/tcp 2>/dev/null || true
  sudo fuser -k 6379/tcp 2>/dev/null || true
  sudo fuser -k 5432/tcp 2>/dev/null || true
fi

# 3. SSL Certificate Setup
echo ""
echo "--- [3/6] Setting up SSL certificates for ${DOMAIN} ---"
sudo mkdir -p "${CERT_DIR}"
sudo mkdir -p /var/www/certbot

if [ ! -f "${CERT_DIR}/fullchain.pem" ] || [ ! -f "${CERT_DIR}/privkey.pem" ]; then
  echo "Attempting to issue Let's Encrypt certificate via Certbot standalone..."
  if command -v certbot >/dev/null 2>&1; then
    if sudo certbot certonly --standalone -d "${DOMAIN}" --email "${EMAIL}" --agree-tos --non-interactive 2>/dev/null; then
      echo "✓ Let's Encrypt certificate obtained successfully!"
    else
      echo "⚠️  Certbot standalone failed (DNS may not have propagated yet). Creating bootstrap certificate..."
      sudo openssl req -x509 -nodes -days 90 -newkey rsa:2048 \
        -keyout "${CERT_DIR}/privkey.pem" \
        -out "${CERT_DIR}/fullchain.pem" \
        -subj "/CN=${DOMAIN}/O=Cenvora/C=US"
      echo "✓ Bootstrap certificate created at ${CERT_DIR}."
    fi
  else
    echo "Certbot not found. Generating bootstrap certificate..."
    sudo openssl req -x509 -nodes -days 90 -newkey rsa:2048 \
      -keyout "${CERT_DIR}/privkey.pem" \
      -out "${CERT_DIR}/fullchain.pem" \
      -subj "/CN=${DOMAIN}/O=Cenvora/C=US"
    echo "✓ Bootstrap certificate created."
  fi
else
  echo "✓ Valid SSL certificate found at ${CERT_DIR}."
fi

# 4. Start Database & Restore Cloudinary Backup
echo ""
echo "--- [4/6] Initializing PostgreSQL & Restoring Backup from Cloudinary ---"
$DOCKER_CMD up -d db redis

echo "Waiting for PostgreSQL to be healthy..."
for i in {1..30}; do
  if $DOCKER_CMD exec -T db pg_isready -U cenvoras_user -d cenvoras_db >/dev/null 2>&1; then
    echo "✓ PostgreSQL is ready."
    break
  fi
  sleep 2
done

# Check if tables exist in the DB, if empty, restore from Cloudinary
TABLE_COUNT=$($DOCKER_CMD exec -T db psql -U cenvoras_user -d cenvoras_db -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';" 2>/dev/null | tr -d '[:space:]' || echo "0")

if [ "$TABLE_COUNT" = "0" ] || [ -z "$TABLE_COUNT" ]; then
  echo "Database is currently empty. Restoring latest Cloudinary backup..."
  if command -v python3 >/dev/null 2>&1; then
    sudo apt-get install -y python3-pip python3-venv >/dev/null 2>&1 || true
    if [ ! -d /tmp/cenvora_venv ]; then
      python3 -m venv /tmp/cenvora_venv 2>/dev/null || true
    fi
    if [ -f /tmp/cenvora_venv/bin/pip ]; then
      /tmp/cenvora_venv/bin/pip install cloudinary python-dotenv --quiet
      /tmp/cenvora_venv/bin/python restore_db_backup.py --apply || echo "⚠️ Backup restore completed with non-fatal notes."
    else
      python3 -m pip install cloudinary python-dotenv --break-system-packages --quiet 2>/dev/null || true
      python3 restore_db_backup.py --apply || echo "⚠️ Backup restore completed with non-fatal notes."
    fi
  else
    echo "⚠️ Python3 not installed on host. Proceeding to migrate directly."
  fi
else
  echo "✓ Existing database tables found (${TABLE_COUNT} tables). Skipping restore."
fi

# 5. Build and Launch Full Cenvora Stack
echo ""
echo "--- [5/6] Building and Starting Full Application Stack ---"
for svc in nginx apache2 caddy; do
  if systemctl is-active --quiet "$svc" 2>/dev/null; then
    sudo systemctl stop "$svc" || true
    sudo systemctl disable "$svc" || true
  fi
done
if command -v fuser >/dev/null 2>&1; then
  sudo fuser -k 80/tcp 2>/dev/null || true
  sudo fuser -k 443/tcp 2>/dev/null || true
fi
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

$DOCKER_CMD $COMPOSE_FILES up --build -d

echo "Waiting 10 seconds for services to initialize..."
sleep 10

echo "Running Django migrations..."
$DOCKER_CMD $COMPOSE_FILES exec -T web python manage.py migrate --noinput

# 6. Verification
echo ""
echo "--- [6/6] Verifying Services & Health ---"
$DOCKER_CMD $COMPOSE_FILES ps

echo ""
echo "Testing local health endpoint..."
if curl -s http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "✓ Web backend is responding healthy!"
else
  echo "⚠️ Backend still initializing, check logs with: $DOCKER_CMD logs -f web"
fi

echo ""
echo "=================================================="
echo "  🎉 CENVORA DEPLOYMENT COMPLETE!"
echo "  API Domain : https://${DOMAIN}"
echo "  Health URL : https://${DOMAIN}/health"
echo "  Container Logs: $DOCKER_CMD logs -f web"
echo "=================================================="
