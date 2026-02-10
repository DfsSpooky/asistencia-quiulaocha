#!/bin/bash
# Script de backup para el sistema de Asistencia Quiulacocha (Docker)

# Cargar variables de entorno
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="./backups"
BACKUP_NAME="backup_asistencia_${TIMESTAMP}"
TEMP_DIR="${BACKUP_DIR}/${BACKUP_NAME}"

# Crear directorios
mkdir -p "${TEMP_DIR}"

echo "--- Iniciando Backup: ${BACKUP_NAME} ---"

# 1. Dump de la Base de Datos (PostgreSQL)
echo "1/3: Exportando base de datos..."
docker compose exec db pg_dump -U ${POSTGRES_USER} ${POSTGRES_DB} > "${TEMP_DIR}/database.sql"

# 2. Backup de Archivos Media (Fotos)
echo "2/3: Comprimiendo archivos multimedia..."
if [ -d "media" ]; then
    tar -czf "${TEMP_DIR}/media.tar.gz" media/
else
    echo "Aviso: No se encontró la carpeta media/."
fi

# 3. Empaquetar todo
echo "3/3: Generando archivo final..."
cd "${BACKUP_DIR}"
tar -czf "${BACKUP_NAME}.tar.gz" "${BACKUP_NAME}"
rm -rf "${BACKUP_NAME}"
cd ..

echo "-------------------------------------------"
echo "¡Backup completado con éxito!"
echo "Archivo: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
echo "-------------------------------------------"
