#!/bin/bash
set -euo pipefail

# Configuration
USER_HESTIA="quiulacocha"
DOMAIN="quiulacocha.theworkpc.com"
APP_DIR="/home/$USER_HESTIA/web/$DOMAIN/public_html"
REPO_URL="https://github.com/Start-Games/asistencia-quiulaocha.git"
BRANCH="main"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Starting Deployment for $DOMAIN...${NC}"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Please run as root (sudo su) to ensure Docker and permissions work correctly.${NC}"
  echo "Try: sudo ./deploy_server.sh"
  exit 1
fi

# 1. Prepare Directory & Permissions
if [ ! -d "$APP_DIR" ]; then
    echo "Creating directory $APP_DIR..."
    mkdir -p "$APP_DIR"
fi

chown -R "$USER_HESTIA:$USER_HESTIA" "$APP_DIR"
cd "$APP_DIR"

# 2. Pull Changes (as service user)
if [ -d ".git" ]; then
    echo "Pulling latest changes..."
    sudo -u "$USER_HESTIA" git pull origin "$BRANCH"
else
    echo "Cloning repository..."
    sudo -u "$USER_HESTIA" git clone "$REPO_URL" .
fi

# 3. Ensure .env
if [ ! -f ".env" ]; then
    echo -e "${RED}WARNING: .env file not found!${NC}"
    echo "Creating a default .env file..."
    cp .env.example .env 2>/dev/null || touch .env

    grep -q "POSTGRES_DB" .env || echo "POSTGRES_DB=asistencia_db" >> .env
    grep -q "POSTGRES_USER" .env || echo "POSTGRES_USER=usuario_db" >> .env
    grep -q "POSTGRES_PASSWORD" .env || echo "POSTGRES_PASSWORD=change_me_please" >> .env
    grep -q "POSTGRES_HOST" .env || echo "POSTGRES_HOST=db" >> .env
    grep -q "POSTGRES_PORT" .env || echo "POSTGRES_PORT=5432" >> .env
    grep -q "ALLOWED_HOSTS" .env || echo "ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1" >> .env
    grep -q "CSRF_TRUSTED_ORIGINS" .env || echo "CSRF_TRUSTED_ORIGINS=https://$DOMAIN" >> .env
    grep -q "DEBUG" .env || echo "DEBUG=False" >> .env
    grep -q "SECRET_KEY" .env || echo "SECRET_KEY=change_me_super_secret_$(date +%s)" >> .env

    chown "$USER_HESTIA:$USER_HESTIA" .env
fi

# 4. Build and Run Docker
echo "Building and starting containers for production..."
docker compose -f docker-compose.yml up -d --build

# 5. Migrations & Static
echo "Running migrations..."
docker compose exec -T web python manage.py migrate

echo "Collecting static files..."
docker compose exec -T web python manage.py collectstatic --noinput

# 6. Final Permission Fix
echo "Fixing ownership for Hestia CP..."
chown -R "$USER_HESTIA:$USER_HESTIA" "$APP_DIR"

# 7. Install Nginx Templates (Automatic)
if [ -d "nginx_hestia_templates" ]; then
    echo "Installing Hestia Nginx proxy templates..."
    NGINX_PROXY_DIR="/usr/local/hestia/data/templates/web/nginx/proxy"
    mkdir -p "$NGINX_PROXY_DIR"
    cp nginx_hestia_templates/django-8000.tpl "$NGINX_PROXY_DIR/"
    cp nginx_hestia_templates/django-8000.stpl "$NGINX_PROXY_DIR/"
    echo "Templates installed to $NGINX_PROXY_DIR/"
    echo "  → django-8000.tpl"
    echo "  → django-8000.stpl"
fi

echo -e "${GREEN}Deployment Finished Successfully!${NC}"
echo "App reachable at http://127.0.0.1:8000 locally."
echo "Ensure your Hestia Proxy Template is set to 'django-8000' or points to port 8000."
