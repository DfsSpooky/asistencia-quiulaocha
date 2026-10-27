#!/bin/bash
# Backup script for Asistencia Quiulacocha (Docker)
set -euo pipefail

# Load env vars
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="./backups"
BACKUP_NAME="backup_asistencia_${TIMESTAMP}"
TEMP_DIR="${BACKUP_DIR}/${BACKUP_NAME}"

mkdir -p "${TEMP_DIR}"

echo "--- Iniciando Backup: ${BACKUP_NAME} ---"

echo "1/3: Exportando base de datos..."
docker compose exec -T db pg_dump \
    -U "${POSTGRES_USER}" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    "${POSTGRES_DB}" > "${TEMP_DIR}/database.sql"

echo "2/3: Comprimiendo archivos multimedia..."
if [ -d "media" ]; then
    tar -czf "${TEMP_DIR}/media.tar.gz" media/
else
    echo "Aviso: No se encontro la carpeta media/."
fi

echo "3/3: Generando archivo final..."
(
    cd "${BACKUP_DIR}"
    tar -czf "${BACKUP_NAME}.tar.gz" "${BACKUP_NAME}"
    rm -rf "${BACKUP_NAME}"
)

echo "-------------------------------------------"
echo "Backup completado con exito"
echo "Archivo: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
echo "-------------------------------------------"
