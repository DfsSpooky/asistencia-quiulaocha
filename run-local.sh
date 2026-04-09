#!/bin/bash
set -euo pipefail

echo "Iniciando entorno local con Docker..."

if [ ! -f .env ]; then
    echo "Creando archivo .env desde .env.example..."
    cp .env.example .env
fi

docker compose up -d --build

echo "Validando arranque del contenedor web..."
docker compose exec web python manage.py check

echo "Listo. La aplicacion esta disponible en:"
echo "- http://localhost:8000"
echo "- http://localhost:8001"
echo "Logs: docker compose logs -f web"
