#!/bin/sh
set -eu

echo "Starting web container..."

DB_HOST_VALUE="${POSTGRES_HOST:-${DB_HOST:-}}"
DB_NAME_VALUE="${POSTGRES_DB:-${DB_NAME:-}}"
DB_USER_VALUE="${POSTGRES_USER:-${DB_USER:-}}"

if [ -n "${DB_HOST_VALUE}" ] && [ -n "${DB_NAME_VALUE}" ] && [ -n "${DB_USER_VALUE}" ]; then
    echo "Waiting for PostgreSQL at ${DB_HOST_VALUE}..."
    ATTEMPTS=0
    until pg_isready -h "${DB_HOST_VALUE}" -U "${DB_USER_VALUE}" -d "${DB_NAME_VALUE}" >/dev/null 2>&1; do
        ATTEMPTS=$((ATTEMPTS + 1))
        if [ "${ATTEMPTS}" -ge 30 ]; then
            echo "PostgreSQL did not become ready in time."
            exit 1
        fi
        sleep 2
    done
fi

echo "Applying migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput

if [ "${SKIP_SERVER:-0}" = "1" ]; then
    echo "Startup preparation completed without launching Gunicorn."
    exit 0
fi

APP_PORT_VALUE="${APP_PORT:-8020}"

echo "Starting Gunicorn..."
exec gunicorn qr_asistencia.wsgi:application --bind "0.0.0.0:${APP_PORT_VALUE}" --timeout 600 --workers 2
