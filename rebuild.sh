# --- Configuration ---
DOCKER_CMD="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  DOCKER_CMD="docker-compose"
fi

echo " ---- Redeploying RPM via $DOCKER_CMD -----"
echo " ---- Pulling latest changes from Git ----- "
if [ -z "$1" ]; then
  echo "Error: No branch name provided."
  echo "Usage: ./rebuild.sh <branch-name>"
  exit 1
fi
git pull origin "$1"

echo " ---- Removing old images ----- "
# Try graceful shutdown
if ! $DOCKER_CMD down; then
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

echo " ---- Building new images ----- "
$DOCKER_CMD up --build -d
$DOCKER_CMD exec web python manage.py migrate
echo " ---- RPM rebuild complete. ----- "
echo "----- Enjoy latest changes -----"