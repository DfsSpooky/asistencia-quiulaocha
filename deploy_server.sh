
#!/bin/bash

# Configuration
USER_HESTIA="quiulacocha"
DOMAIN="quiulacocha.theworkpc.com"
APP_DIR="/home/$USER_HESTIA/web/$DOMAIN/public_html" # Changing to public_html as it always exists and has permissions.
REPO_URL="https://github.com/Start-Games/asistencia-quiulaocha.git" # Replace with actual repo if different, assuming public or keys setup.
BRANCH="main" # or master

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting Deployment for $DOMAIN...${NC}"

# 1. Prepare Directory
if [ ! -d "$APP_DIR" ]; then
    echo "Creating directory $APP_DIR..."
    mkdir -p "$APP_DIR"
fi

cd "$APP_DIR"

# 2. Pull Changes
if [ -d ".git" ]; then
    echo "Pulling latest changes..."
    git pull origin $BRANCH
else
    echo "Cloning repository..."
    # If the directory is not empty but not a git repo, you might need to handle it.
    # Assuming it's empty or we can clone into .
    git clone $REPO_URL .
fi

# 3. Check .env
if [ ! -f ".env" ]; then
    echo -e "${RED}WARNING: .env file not found!${NC}"
    echo "Creating a default .env file. PLEASE EDIT IT with correct secrets."
    cp .env.example .env 2>/dev/null || touch .env
    
    # Add vital vars if missing
    grep -q "POSTGRES_DB" .env || echo "POSTGRES_DB=asistencia_db" >> .env
    grep -q "POSTGRES_USER" .env || echo "POSTGRES_USER=usuario_db" >> .env
    grep -q "POSTGRES_PASSWORD" .env || echo "POSTGRES_PASSWORD=change_me_please" >> .env
    grep -q "POSTGRES_HOST" .env || echo "POSTGRES_HOST=db" >> .env
    grep -q "ALLOWED_HOSTS" .env || echo "ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1" >> .env
    grep -q "CSRF_TRUSTED_ORIGINS" .env || echo "CSRF_TRUSTED_ORIGINS=https://$DOMAIN" >> .env
fi

# 4. Build and Run Docker
echo "Building and starting containers..."
docker compose up -d --build

# 5. Run Migrations & Collect Static
echo "Running migrations..."
docker compose exec web python manage.py migrate

echo "Collecting static files..."
docker compose exec web python manage.py collectstatic --noinput

# 6. Check Status
if [ $? -eq 0 ]; then
    echo -e "${GREEN}Deployment Finished Successfully!${NC}"
    echo "App reachable at http://127.0.0.1:8000 locally."
    echo "Ensure your Hestia Proxy Template is set to 'django-8000' or points to port 8000."
else
    echo -e "${RED}Deployment Failed! Check logs with: docker compose logs -f${NC}"
fi
