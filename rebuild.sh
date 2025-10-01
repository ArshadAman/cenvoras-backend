echo " ---- Redeploying RPM -----"
echo " ---- Pulling latest changes from Git ----- "
if [ -z "$1" ]; then
  echo "Error: No branch name provided."
  echo "Usage: ./git-pull-branch.sh <branch-name>"
  exit 1
fi
git pull origin "$1"
echo " ---- Removing old images ----- "
docker-compose down
echo " ---- Building new images ----- "
docker-compose up --build -d
docker-compose exec web python manage.py migrate
echo " ---- RPM rebuild complete. ----- "
echo "----- Enjoy latest changes -----"