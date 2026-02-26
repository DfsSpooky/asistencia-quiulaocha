#!/bin/bash
# Script de restauración para el sistema de Asistencia Quiulacocha (Docker)

if [ -z "$1" ]; then
    echo "Error: Debes especificar el archivo de backup (ej: ./backups/backup_xxxx.tar.gz)"
    exit 1
fi

BACKUP_FILE=$1
TEMP_RESTORE_DIR="./temp_restore"

# Cargar variables de entorno
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

echo "--- Iniciando Restauración desde: ${BACKUP_FILE} ---"

# 1. Preparar archivos
mkdir -p "${TEMP_RESTORE_DIR}"
tar -xzf "${BACKUP_FILE}" -C "${TEMP_RESTORE_DIR}"
# Obtener el nombre de la carpeta interna (sin el .tar.gz)
INTERNAL_DIR=$(ls "${TEMP_RESTORE_DIR}")

# 2. Restaurar Base de Datos
echo "1/2: Restaurando base de datos..."
# Dropear y recrear la DB pública (o usar --clean en pg_dump)
# Como el dump no tiene --clean, dropeamos esquemas o usamos este truco:
cat "${TEMP_RESTORE_DIR}/${INTERNAL_DIR}/database.sql" | docker compose exec -T db psql -U ${POSTGRES_USER} ${POSTGRES_DB}

# 3. Restaurar Media
echo "2/2: Restaurando archivos multimedia..."
if [ -f "${TEMP_RESTORE_DIR}/${INTERNAL_DIR}/media.tar.gz" ]; then
    rm -rf media/
    tar -xzf "${TEMP_RESTORE_DIR}/${INTERNAL_DIR}/media.tar.gz"
fi

# Limpieza
rm -rf "${TEMP_RESTORE_DIR}"

echo "-------------------------------------------"
echo "¡SISTEMA RESTAURADO EXITOSAMENTE!"
echo "-------------------------------------------"
