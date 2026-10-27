# Guía de Despliegue en Coolify - Sistema de Asistencia Quiulacocha

Esta guía explica paso a paso cómo desplegar el sistema en **Coolify** de forma rápida, robusta y con certificados SSL automáticos.

---

## 🚀 Método Recomendado: Docker Compose (Todo en Uno)

Coolify permite desplegar aplicaciones completas (Django + PostgreSQL) usando el archivo optimizado [`docker-compose.coolify.yml`](./docker-compose.coolify.yml).

### Paso 1: Crear el recurso en Coolify
1. Abre tu panel de Coolify.
2. Ve a tu **Project** y selecciona el **Environment** (ej. `production` o `staging`).
3. Haz clic en **+ New Resource**.
4. Selecciona **Docker Compose**.
5. Elige **Git Repository**:
   - **Repository URL:** `https://github.com/tu-usuario/asistencia-quiulaocha` (o tu repositorio).
   - **Branch:** `Implementacion-carnets-despliegue` (o `main`).
   - **Compose File Path:** `docker-compose.coolify.yml`

### Paso 2: Configurar el Dominio
En la configuración del servicio `web` en Coolify:
- En **Domains / FQDN**, coloca tu dominio con https:
  ```
  https://asistencia.tudominio.com
  ```
- Coolify configurará automáticamente el proxy Traefik y el certificado SSL con Let's Encrypt.

### Paso 3: Configurar las Variables de Entorno (Environment Variables)
En la pestaña **Environment Variables** de Coolify, agrega las siguientes variables:

```env
# Clave secreta de Django (genera una cadena segura)
SECRET_KEY=django-insecure-cambia-esta-clave-por-una-super-secreta-2026

# Modo producción
DEBUG=False

# Hosts y dominios permitidos
ALLOWED_HOSTS=*
CSRF_TRUSTED_ORIGINS=https://asistencia.tudominio.com

# Base de datos PostgreSQL interna del compose
POSTGRES_DB=asistencia_db
POSTGRES_USER=asistencia_user
POSTGRES_PASSWORD=ColocaAquiUnaContrasenaSegura123!

# Configuración de archivos y proxy
SERVE_MEDIA_FILES=True
APP_PORT=8020
SECURE_SSL_REDIRECT=False
```

> **Nota de Seguridad:** Coolify ya maneja la redirección HTTPS en el proxy (Traefik), por lo que `SECURE_SSL_REDIRECT=False` evita bucles de redirección internos y asegura que los healthchecks funcionen al 100%.

### Paso 4: Desplegar
Haz clic en el botón **Deploy** en la esquina superior derecha.
Coolify:
1. Construirá la imagen Docker de Django.
2. Levantará PostgreSQL con volumen persistente (`postgres_data`).
3. Ejecutará automáticamente `python manage.py migrate` y `collectstatic`.
4. Levantará el servidor Gunicorn en el puerto 8020.
5. Dejará el sitio accesible en `https://asistencia.tudominio.com`.

---

## 📦 Restauración de Datos y Comuneros

Una vez que el sistema esté desplegado, tienes 2 formas de restaurar los 584 comuneros, fotos y reuniones históricas:

### Opción A (Desde la Web - Más Fácil):
1. Ingresa al sistema en tu navegador con las credenciales de superusuario:
   `https://asistencia.tudominio.com/gestionsegura/`
2. En el panel principal o en el menú de mantenimiento, abre el modal **Restaurar Backup**.
3. Selecciona tu archivo de backup (ej: `backup_asistencia_20261016_200633.tar.gz`).
4. Haz clic en **Restaurar**. El sistema importará automáticamente la base de datos SQL y las fotos en media.

### Opción B (Desde la Terminal de Coolify):
1. En Coolify, ve al servicio `web` y abre la pestaña **Terminal**.
2. O desde el servidor VPS mediante SSH, ejecuta:
   ```bash
   bash scripts/restore.sh ./backups/backup_asistencia_20261016_200633.tar.gz
   ```

---

## 🔐 URLs Principales del Sistema

- **Página de Inicio / Landing:** `https://asistencia.tudominio.com/`
- **Panel Administrativo Django:** `https://asistencia.tudominio.com/gestionsegura/`
- **Escaneo QR de Asistencia:** `https://asistencia.tudominio.com/escanear/`
- **Verificación de Salud (Healthcheck):** `https://asistencia.tudominio.com/healthz/`
