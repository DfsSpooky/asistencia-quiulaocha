#!/bin/bash
# Restore script for Asistencia Quiulacocha (Docker)
set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Error: Debes especificar el archivo de backup (ej: ./backups/backup_xxxx.tar.gz)"
    exit 1
fi

BACKUP_FILE="$1"
TEMP_RESTORE_DIR="./temp_restore"

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "Error: El archivo de backup no existe: ${BACKUP_FILE}"
    exit 1
fi

# Load env vars
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

echo "--- Iniciando Restauracion desde: ${BACKUP_FILE} ---"

mkdir -p "${TEMP_RESTORE_DIR}"
trap 'rm -rf "${TEMP_RESTORE_DIR}"' EXIT

tar -xzf "${BACKUP_FILE}" -C "${TEMP_RESTORE_DIR}"
INTERNAL_DIR=$(ls "${TEMP_RESTORE_DIR}")

echo "1/2: Restaurando base de datos..."
cat "${TEMP_RESTORE_DIR}/${INTERNAL_DIR}/database.sql" | docker compose exec -T db psql \
    -v ON_ERROR_STOP=1 \
    --single-transaction \
    -U "${POSTGRES_USER}" "${POSTGRES_DB}"

echo "2/2: Restaurando archivos multimedia..."
if [ -f "${TEMP_RESTORE_DIR}/${INTERNAL_DIR}/media.tar.gz" ]; then
    rm -rf media/
    tar -xzf "${TEMP_RESTORE_DIR}/${INTERNAL_DIR}/media.tar.gz"
fi

echo "-------------------------------------------"
echo "SISTEMA RESTAURADO EXITOSAMENTE"
echo "-------------------------------------------"
