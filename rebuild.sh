echo " ---- Redeploying RPM -----"
echo " ---- Pulling latest changes from Git ----- "
if [ -z "$1" ]; then
  echo "Error: No branch name provided."
  echo "Usage: ./git-pull-branch.sh <branch-name>"
  exit 1
fi
git pull origin "$1"
echo " ---- Removing old images ----- "
# Try graceful shutdown first
if ! docker compose down; then
  echo " ---- Warning: Graceful shutdown failed. Attempting to force remove containers... ----- "
  docker rm -f cenvoras-backend-web-1 cenvoras-backend-celery_worker-1 cenvoras-backend-celery_beat-1 cenvoras-backend-nginx-1 2>/dev/null
  
  # Check if containers are still alive (permission denied might have blocked rm -f too)
  if docker ps -a | grep -q "cenvoras-backend"; then
    echo " ---- Critical: Containers still stuck. Restarting Docker service to clear filesystem locks... ----- "
    systemctl restart docker || service docker restart
    # Give it a second to wake up
    sleep 2
    # Final cleanup attempt
    docker compose down 2>/dev/null
  fi
  
  # Clean up any dangling networks
  docker network prune -f
fi

echo " ---- Building new images ----- "
docker compose up --build -d
docker compose exec web python manage.py migrate
echo " ---- RPM rebuild complete. ----- "
echo "----- Enjoy latest changes -----"