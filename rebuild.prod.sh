#!/bin/bash
# Production rebuild script
# Usage: ./rebuild.prod.sh <branch-name>

# --- Configuration ---
DOCKER_CMD="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  DOCKER_CMD="docker-compose"
fi

COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

echo " ---- Redeploying Cenvoras (PRODUCTION) via $DOCKER_CMD -----"
echo " ---- Using Let's Encrypt certificates -----"
echo " ---- Pulling latest changes from Git ----- "

if [ -z "$1" ]; then
  echo "Error: No branch name provided."
  echo "Usage: ./rebuild.prod.sh <branch-name>"
  exit 1
fi

git pull origin "$1"

echo " ---- Removing old images ----- "
# Try graceful shutdown with production override
if ! $DOCKER_CMD $COMPOSE_FILES down; then
  echo " ---- Warning: Shutdown failed. Attempting force removal... ----- "
  
  # The 'permission denied' error on stop is often an AppArmor sync issue.
  # This command refreshes AppArmor profiles and often fixes the 'permission denied' bug.
  if command -v aa-remove-unknown >/dev/null 2>&1; then
    echo " ---- Cleaning AppArmor profiles... ----- "
    aa-remove-unknown 2>/dev/null
  fi

  # Force kill the specific containers
  docker rm -f cenvoras-backend-web-1 cenvoras-backend-celery_worker-1 cenvoras-backend-celery_beat-1 cenvoras-backend-nginx-1 2>/dev/null
  
  # If still stuck, restart the service as a last-ditch effort
  if docker ps -a | grep -q "cenvoras-backend"; then
    echo " ---- Critical: Containers still stuck. Restarting Docker service... ----- "
    systemctl restart docker || service docker restart
    sleep 2
  fi
  
  docker network prune -f
fi

echo " ---- Building new images with production overrides ----- "
$DOCKER_CMD $COMPOSE_FILES up --build -d

echo " ---- Waiting for services to be healthy ----- "
sleep 10

echo " ---- Running database migrations ----- "
$DOCKER_CMD $COMPOSE_FILES exec -T web python manage.py migrate

echo " ---- Verifying services ----- "
$DOCKER_CMD $COMPOSE_FILES ps

echo " ---- Production rebuild complete. ----- "
echo " ---- Certificates: /etc/letsencrypt/live/api.cenvora.app/ -----"
echo " ---- Nginx logs: docker-compose -f docker-compose.yml -f docker-compose.prod.yml logs nginx -----"
echo "----- Ready for production traffic -----"
