#!/bin/bash
set -euo pipefail

# Configuration
USER_HESTIA="${USER_HESTIA:-aquiulacocha}"
DOMAIN="${DOMAIN:-quiulacocha.theworkpc.com}"
SITE_ROOT="/home/$USER_HESTIA/web/$DOMAIN"
APP_DIR="$SITE_ROOT/app"
PUBLIC_DIR="$SITE_ROOT/public_html"
PRIVATE_DIR="$SITE_ROOT/private"
MEDIA_DIR="$PRIVATE_DIR/media"
ENV_FILE="$PRIVATE_DIR/.env"
REPO_URL="${REPO_URL:-https://github.com/DfsSpooky/asistencia-quiulaocha.git}"
BRANCH="${BRANCH:-despliegue-local-y-produccion}"

generate_secret_key() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(50))
PY
    fi
}

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

# 1. Prepare Directory Layout
echo "Preparing secure directory layout..."
mkdir -p "$APP_DIR" "$PUBLIC_DIR/static" "$MEDIA_DIR"
chown -R "$USER_HESTIA:$USER_HESTIA" "$SITE_ROOT"

# 2. Pull Changes (as service user)
cd "$SITE_ROOT"
if [ -d ".git" ]; then
    echo "Unexpected git repository at $SITE_ROOT; this script expects the repo inside $APP_DIR."
    exit 1
fi

if [ -d "$APP_DIR/.git" ]; then
    echo "Pulling latest changes into $APP_DIR..."
    cd "$APP_DIR"
    sudo -u "$USER_HESTIA" git pull origin "$BRANCH"
else
    echo "Cloning repository into $APP_DIR..."
    rm -rf "$APP_DIR"
    sudo -u "$USER_HESTIA" git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# 3. Ensure secure .env outside public_html
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}WARNING: .env file not found!${NC}"
    echo "Creating a default secure .env at $ENV_FILE..."
    cp .env.example "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"

    grep -q "POSTGRES_DB" "$ENV_FILE" || echo "POSTGRES_DB=asistencia_db" >> "$ENV_FILE"
    grep -q "POSTGRES_USER" "$ENV_FILE" || echo "POSTGRES_USER=postgres" >> "$ENV_FILE"
    grep -q "POSTGRES_PASSWORD" "$ENV_FILE" || echo "POSTGRES_PASSWORD=change_me_please" >> "$ENV_FILE"
    grep -q "POSTGRES_HOST" "$ENV_FILE" || echo "POSTGRES_HOST=db" >> "$ENV_FILE"
    grep -q "POSTGRES_PORT" "$ENV_FILE" || echo "POSTGRES_PORT=5432" >> "$ENV_FILE"
    grep -q "ALLOWED_HOSTS" "$ENV_FILE" || echo "ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1" >> "$ENV_FILE"
    grep -q "CSRF_TRUSTED_ORIGINS" "$ENV_FILE" || echo "CSRF_TRUSTED_ORIGINS=https://$DOMAIN" >> "$ENV_FILE"
    grep -q "SECURE_SSL_REDIRECT" "$ENV_FILE" || echo "SECURE_SSL_REDIRECT=True" >> "$ENV_FILE"
    grep -q "SESSION_COOKIE_SECURE" "$ENV_FILE" || echo "SESSION_COOKIE_SECURE=True" >> "$ENV_FILE"
    grep -q "CSRF_COOKIE_SECURE" "$ENV_FILE" || echo "CSRF_COOKIE_SECURE=True" >> "$ENV_FILE"
    grep -q "DEBUG" "$ENV_FILE" || echo "DEBUG=False" >> "$ENV_FILE"
    grep -q "SECRET_KEY" "$ENV_FILE" || echo "SECRET_KEY=$(generate_secret_key)" >> "$ENV_FILE"
    # Clave dedicada para firma de QR. Debe mantenerse estable entre migraciones de servidor.
    grep -q "QR_SIGNING_KEY" "$ENV_FILE" || echo "QR_SIGNING_KEY=$(generate_secret_key)" >> "$ENV_FILE"
    grep -q "QR_SIGNING_FALLBACK_KEYS" "$ENV_FILE" || echo "QR_SIGNING_FALLBACK_KEYS=" >> "$ENV_FILE"

    chown "$USER_HESTIA:$USER_HESTIA" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

# 4. Build and Run Docker
echo "Building and starting containers for production..."
cd "$APP_DIR"
docker compose -f docker-compose.yml up -d --build

# 5. Sanity Check
echo "Running Django system check..."
docker compose -f docker-compose.yml exec -T web python manage.py check

echo "Current container status:"
docker compose -f docker-compose.yml ps

# 6. Final Permission Fix
echo "Fixing ownership for Hestia CP..."
chown -R "$USER_HESTIA:$USER_HESTIA" "$SITE_ROOT"
find "$PUBLIC_DIR" -type d -exec chmod 755 {} \;
find "$PUBLIC_DIR" -type f -exec chmod 644 {} \;
find "$PRIVATE_DIR" -type d -exec chmod 750 {} \;
find "$PRIVATE_DIR" -type f -exec chmod 640 {} \;
chmod 600 "$ENV_FILE"

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
echo "Hestia public_html now contains only static assets in $PUBLIC_DIR/static."
echo "Ensure your Hestia Proxy Template is set to 'django-8000' and points to port 8000."
