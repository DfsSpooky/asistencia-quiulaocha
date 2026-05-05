#!/bin/bash

echo "🚀 Iniciando entorno local con Docker..."

# Asegurarse de que el archivo .env exista
if [ ! -f .env ]; then
    echo "📄 Creando archivo .env desde .env.example..."
    cp .env.example .env
fi

# Levantar servicios
docker compose up -d --build

echo "⏳ Esperando a que la base de datos esté lista..."
sleep 5

# Ejecutar migraciones
echo "📂 Ejecutando migraciones..."
docker compose exec web python manage.py migrate

# Recolectar archivos estáticos
echo "🎨 Recolectando archivos estáticos..."
docker compose exec web python manage.py collectstatic --noinput

echo "✅ ¡Listo! La aplicación está corriendo en: http://localhost:8000"
echo "💡 Puedes ver los logs con: docker compose logs -f web"
