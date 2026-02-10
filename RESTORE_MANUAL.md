# Manual de Restauración de Emergencia - Quiulacocha

Si el sistema sufre una pérdida de datos o necesitas restaurar un backup anterior, sigue estos pasos.

### Prerrequisitos
- Acceso a la terminal del servidor.
- Docker y Docker Compose instalados.
- El archivo de backup (ej: `backup_asistencia_20260210_123000.tar.gz`).

### Pasos para Restaurar

1.  **Ubica tu backup**: Los backups generados por el sistema se guardan en la carpeta `backups/`.
2.  **Asegúrate de que los contenedores estén corriendo**:
    ```bash
    docker compose up -d
    ```
3.  **Ejecuta el script de restauración**:
    Dale permisos de ejecución si no los tiene:
    ```bash
    chmod +x scripts/restore.sh
    ```
    Y lanza la restauración especificando el archivo:
    ```bash
    ./scripts/restore.sh ./backups/tu_archivo_de_backup.tar.gz
    ```

### ¿Qué hace este script?
1.  Extrae los archivos SQL y las fotos del backup.
2.  Limpia la base de datos actual e importa el SQL.
3.  Reemplaza la carpeta `media/` con las fotos del backup.

> [!CAUTION]
> Este proceso es irreversible y sobrescribirá los datos actuales con los del backup seleccionado. Úsalo solo en emergencias o migraciones.
