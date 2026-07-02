# Guía de Despliegue en Coolify

Esta guía describe cómo desplegar el **Sistema de Asistencia QR** en **Coolify** de manera rápida, segura y usando el estándar en la nube (Docker + WhiteNoise).

---

## 🛠️ Arquitectura en Coolify

Al desplegar en Coolify, utilizaremos una arquitectura simplificada y moderna:
1. **Base de Datos (Servicio Independiente):** PostgreSQL gestionado por Coolify.
2. **Aplicación Web (Servicio Docker):** Contenedor autoejecutable que corre Gunicorn.
3. **Servicio de Archivos Estáticos (WhiteNoise):** Django se encarga de servir sus propios archivos estáticos (`/static/`) con compresión y caché de forma ultraeficiente.
4. **Almacenamiento Persistente (Volumen Docker):** Para los archivos de medios (`/app/media`) como fotos y PDFs generados, evitando que se borren al actualizar el código.

---

## 📋 Pasos para el Despliegue

### Paso 1. Crear la Base de Datos PostgreSQL
1. En tu panel de Coolify, ve al proyecto/entorno donde deseas desplegar.
2. Haz clic en **New Resource** (Nuevo Recurso) -> **Databases** -> **PostgreSQL**.
3. Elige el servidor y la versión (se recomienda PostgreSQL 15 o posterior).
4. Deja que Coolify genere las credenciales automáticamente y espera a que la base de datos se inicie.
5. Copia los siguientes valores de la sección de credenciales de la base de datos:
   - **Postgres Database Name**
   - **Postgres User**
   - **Postgres Password**
   - **Internal Host** (la URL o nombre interno del contenedor de la base de datos, por ejemplo `postgresql-12345`)
   - **Internal Port** (por defecto `5432`)

---

### Paso 2. Crear la Aplicación Web
1. En el mismo entorno de Coolify, haz clic en **New Resource** -> **Application** -> **GitHub Repository**.
2. Conecta tu cuenta de GitHub (si no lo has hecho) y selecciona el repositorio `asistencia-quiulaocha`.
3. Selecciona la rama de producción correspondiente (por ejemplo `main` o la que estés usando).
4. En **Build Pack**, selecciona **Dockerfile**.
5. Deja los puertos por defecto o asegúrate de que el puerto expuesto en Coolify coincida con el del Dockerfile (`8020`).
6. Configura el dominio de producción que deseas asignar a la web (por ejemplo `https://asistencia.tudominio.com`). Coolify se encargará de gestionar el certificado SSL automáticamente.

---

### Paso 3. Configurar Almacenamiento Persistente (Media)
Los archivos subidos por los usuarios o generados por el sistema (como las plantillas PDF) se guardan en la carpeta `/app/media`. Para evitar perderlos en cada actualización de código:
1. Ve a la configuración de tu aplicación en Coolify.
2. Abre la pestaña **Storage** (Almacenamiento).
3. Agrega un volumen persistente:
   - **Volume Name:** `media-data` (o el nombre que prefieras)
   - **Path inside container:** `/app/media`
4. Guarda los cambios.

---

### Paso 4. Configurar Variables de Entorno (Environment Variables)
Ve a la pestaña **Environment Variables** en la configuración de la aplicación web en Coolify y agrega las siguientes variables:

| Variable | Valor Sugerido | Descripción |
| :--- | :--- | :--- |
| `DEBUG` | `False` | Desactiva el modo depuración para producción. |
| `SECRET_KEY` | *(Una cadena larga y aleatoria)* | Genera una clave segura. |
| `QR_SIGNING_KEY` | *(Una cadena larga y aleatoria)* | Clave para firmar los códigos QR de los carnets. |
| `ALLOWED_HOSTS` | `asistencia.tudominio.com,localhost,127.0.0.1` | El dominio que configuraste (separa por comas). |
| `CSRF_TRUSTED_ORIGINS` | `https://asistencia.tudominio.com` | Tu dominio con protocolo `https://`. |
| `SECURE_SSL_REDIRECT` | `True` | Redirige todo el tráfico HTTP a HTTPS. |
| `SESSION_COOKIE_SECURE` | `True` | Cookies de sesión seguras mediante HTTPS. |
| `CSRF_COOKIE_SECURE` | `True` | Cookies CSRF seguras mediante HTTPS. |
| `SERVE_MEDIA_FILES` | `True` | Permite que Django sirva los archivos de la carpeta `/media/` en producción. |
| `POSTGRES_DB` | *(De las credenciales de Coolify DB)* | Nombre de la base de datos PostgreSQL. |
| `POSTGRES_USER` | *(De las credenciales de Coolify DB)* | Usuario de PostgreSQL. |
| `POSTGRES_PASSWORD` | *(De las credenciales de Coolify DB)* | Contraseña de PostgreSQL. |
| `POSTGRES_HOST` | *(De las credenciales de Coolify DB)* | Host interno de PostgreSQL en Coolify (ej. `postgresql-12345`). |
| `POSTGRES_PORT` | `5432` | Puerto interno de la base de datos. |

> [!TIP]
> Puedes generar una clave segura para `SECRET_KEY` y `QR_SIGNING_KEY` ejecutando este comando en tu terminal local:
> ```bash
> openssl rand -hex 32
> ```

---

### Paso 5. Desplegar la Aplicación
1. Una vez guardadas las variables de entorno y configurado el volumen, haz clic en el botón **Deploy** (Desplegar).
2. Durante el inicio del contenedor, se ejecutarán automáticamente las siguientes tareas gracias a nuestro script `start-web.sh`:
   - Espera a que PostgreSQL esté listo.
   - Aplica las migraciones a la base de datos (`python manage.py migrate`).
   - Recopila los archivos estáticos (`python manage.py collectstatic`).
   - Inicia el servidor Gunicorn en el puerto `8020`.

---

### Paso 6. Tareas de Post-Despliegue (Terminal en Coolify)
Una vez que el despliegue termine con éxito y la web responda, necesitarás realizar dos acciones iniciales:

#### 1. Crear el Superusuario Administrador
Para poder iniciar sesión en el panel administrativo (`/gestionsegura/`):
1. Entra a la aplicación en Coolify.
2. Ve a la pestaña **Terminal**.
3. Ejecuta el comando:
   ```bash
   python manage.py createsuperuser
   ```
4. Sigue las instrucciones interactivas en la consola para asignar el nombre de usuario, correo y contraseña.

#### 2. Importar el Padrón de Socios (CSV)
Si tienes el archivo `Padron usuarios.csv` en la raíz del proyecto para importar tus usuarios de forma masiva:
1. Desde la misma pestaña **Terminal** de Coolify, ejecuta:
   ```bash
   python manage.py importar_padron
   ```

---

## 🔒 Consejos de Seguridad
- Nunca compartas los valores de `SECRET_KEY` o `POSTGRES_PASSWORD`.
- Asegúrate de que las copias de seguridad automáticas estén activadas en la base de datos de PostgreSQL dentro de Coolify.
- Siempre mantén `DEBUG` en `False` en producción.
