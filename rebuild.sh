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
  echo " ---- Warning: Graceful shutdown failed, attempting force removal of stuck containers ----- "
  # Force remove known container names if they are stuck
  docker rm -f cenvoras-backend-web-1 cenvoras-backend-celery_worker-1 cenvoras-backend-celery_beat-1 cenvoras-backend-nginx-1 2>/dev/null
  # Clean up any dangling networks if down failed halfway
  docker network prune -f
fi

echo " ---- Building new images ----- "
docker compose up --build -d
docker compose exec web python manage.py migrate
echo " ---- RPM rebuild complete. ----- "
echo "----- Enjoy latest changes -----"