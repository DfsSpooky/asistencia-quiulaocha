# Guia de Despliegue en Hestia CP con Docker

Esta guía detalla paso a paso cómo desplegar la aplicación `qr_asistencia` utilizando Docker y Docker Compose en un servidor gestionado por Hestia CP.

## Requisitos Previos

1.  **Servidor VPS con Hestia CP instalado.**
2.  **Acceso SSH al servidor (root o usuario con sudo).**
3.  **Dominio configurado en Hestia CP.**

## Paso 1: Instalar Docker y Docker Compose

Si tu servidor aún no tiene Docker instalado, ejecuta los siguientes comandos vía SSH:

```bash
# Actualizar repositorios
sudo apt update

# Instalar requisitos
sudo apt install apt-transport-https ca-certificates curl software-properties-common -y

# Añadir llave GPG de Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

# Añadir repositorio de Docker
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Instalar Docker
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-compose-plugin -y

# Verificar instalación
docker --version
docker compose version
```

## Paso 2: Preparar el Proyecto en el Servidor

1.  **Navega al directorio de tu usuario en Hestia:**
    Reemplaza `usuario_hestia` y `tu_dominio.com` con tus datos.

    ```bash
    cd /home/usuario_hestia/web/tu_dominio.com/public_html
    ```

    > **Nota:** Puedes borrar el contenido por defecto de `public_html` si deseas que la aplicación responda directamente desde la raíz, pero usaremos un puerto específico, así que el código puede estar en una carpeta separada si prefieres, por ejemplo `/home/usuario_hestia/web/tu_dominio.com/app`. Para simplificar, asumiremos que subes los archivos a una carpeta `app` dentro de `public_html` o al nivel de `web`.
    >
    > **Recomendación:** Crea una carpeta para la app fuera de `public_html` para evitar exponer código fuente accidentalmente si falla la configuración, o asegúrate de que Nginx no sirva los archivos `.py`.
    
    ```bash
    mkdir -p /home/usuario_hestia/web/tu_dominio.com/app
    cd /home/usuario_hestia/web/tu_dominio.com/app
    ```

2.  **Subir los archivos:**
    Puedes usar Git para clonar el repositorio o subir los archivos vía SFTP/File Manager. Asegúrate de incluir:
    - `Dockerfile`
    - `docker-compose.yml`
    - `requirements.txt`
    - `manage.py`
    - Carpetas `qr_asistencia`, `asistencia`, `templates`, etc.

3.  **Configurar variables de entorno:**
    Crea un archivo `.env` basado en el ejemplo:

    ```bash
    nano .env
    ```

    Pega el contenido y ajusta las contraseñas:

    ```env
    DEBUG=False
    SECRET_KEY=cambia_esto_por_una_llave_segura_y_larga
    ALLOWED_HOSTS=tu_dominio.com,www.tu_dominio.com,localhost
    
    POSTGRES_DB=asistencia_db
    POSTGRES_USER=usuario_db
    POSTGRES_PASSWORD=contrasena_segura_db
    POSTGRES_HOST=db
    POSTGRES_PORT=5432
    ```

## Paso 3: Iniciar los Contenedores

Dentro de la carpeta donde está el `docker-compose.yml`:

```bash
# Construir y levantar servicios en segundo plano
docker compose up -d --build
```

Verifica qeu todo esté corriendo:

```bash
docker compose ps
```

Deberías ver los servicios `web` y `db` en estado "Up".

## Paso 4: Migraciones y Archivos Estáticos

Una vez que los contenedores estén corriendo, ejecuta las migraciones y colecta los estáticos:

```bash
# Ejecutar migraciones
docker compose exec web python manage.py migrate

# Colectar archivos estáticos
docker compose exec web python manage.py collectstatic --noinput

# Crear superusuario (opcional, seguir instrucciones en pantalla)
docker compose exec web python manage.py createsuperuser
```

## Paso 5: Configurar Proxy Inverso en Hestia CP

Ahora necesitamos que Hestia envíe el tráfico web (puerto 80/443) al puerto 8000 de nuestro contenedor Docker.

1.  Ingresa al panel de administración de Hestia CP.
2.  Ve a la sección **WEB** y edita el dominio `tu_dominio.com`.
3.  Busca la opción **Plantilla Proxy** (Proxy Template) o **Nginx Template**.
    - Hestia suele tener una plantilla llamada `proxy` o `docker-proxy`. Si está disponible, selecciónala.
    - **Si no tienes una plantilla predefinida para puerto 8000**, o la plantilla default apunta a otro puerto, tendremos que crear una o usar la configuración personalizada.

### Opción A: Configuración Personalizada Nginx (Recomendado)

Hestia permite configuraciones personalizadas de Nginx para cada dominio.

1.  En la configuración del dominio en Hestia, asegúrate de que **Soporte Proxy** esté habilitado (Nginx).
2.  Desplázate hacia abajo hasta "Opciones avanzadas".
3.  En "Plantilla Proxy", selecciona **default** (o **force-https** si usas SSL, que deberías).
4.  Guarda los cambios.

Ahora, editaremos manualmente la configuración del proxy via SSH o creando una plantilla personalizada. La forma más rápida y segura en Hestia sin crear plantillas globales es editar el archivo de configuración custom del usuario, pero lo ideal es crear una plantilla.

**Crear una plantilla simple para puerto 8000:**

1.  Vía SSH, crea un archivo de plantilla:
    ```bash
    cp /usr/local/hestia/data/templates/web/nginx/proxy/default.tpl /usr/local/hestia/data/templates/web/nginx/proxy/django-8000.tpl
    cp /usr/local/hestia/data/templates/web/nginx/proxy/default.stpl /usr/local/hestia/data/templates/web/nginx/proxy/django-8000.stpl
    ```

2.  Edita ambos archivos (`.tpl` y `.stpl`) y busca la línea `proxy_pass`. Cambia el puerto destino:
    
    ```nginx
    # Buscar:
    proxy_pass      http://%ip%:%web_port%;
    
    # Cambiar por:
    proxy_pass      http://127.0.0.1:8000;
    ```
    
    Configura también los estáticos si deseas que Nginx los sirva directamente (más rápido), mapeando la ubicación `/static/` a la ruta `staticfiles` del volumen (requiere que el volumen sea accesible por el host o configurar alias).
    
    Para simplificar, dejemos que todo pase al contenedor (Gunicorn + WhiteNoise si estuviera configurado, pero como tenemos Nginx en Hestia, el `proxy_pass` es suficiente para el tráfico dinámico).

    **Nota:** Si solo cambias el `proxy_pass` a `http://127.0.0.1:8000`, todo el tráfico irá al contenedor. Asegúrate de que Gunicorn esté sirviendo los estáticos (con WhiteNoise) O configura un alias en el Nginx de Hestia para `/static/`.

    **Configuración recomendada para Nginx en Hestia (agregando alias para static):**

    En tu plantilla `.tpl` y `.stpl`, antes del bloque `location /`, agrega:

    ```nginx
    location /static/ {
        alias /home/usuario_hestia/web/tu_dominio.com/app/staticfiles/;
    }
    
    location /media/ {
        alias /home/usuario_hestia/web/tu_dominio.com/app/media/;
    }
    ```

3.  Guarda los archivos.
4.  Vuelve al panel de Hestia, edita el dominio Web, y en **Plantilla Proxy**, ahora deberías ver `django-8000`. Selecciónala y guarda.

## Paso 6: SSL (HTTPS)

1.  En la edición del dominio web en Hestia, habilita **Soporte SSL** y **Lets Encrypt Support**.
2.  Guarda. Hestia generará los certificados y recargará Nginx.

¡Listo! Tu aplicación debería estar accesible en `https://tu_dominio.com`.

## Mantenimiento

- **Ver logs:** `docker compose logs -f`
- **Reiniciar:** `docker compose restart`
- **Actualizar cambios:**
    1.  `git pull` (o subir archivos nuevos)
    2.  `docker compose up -d --build`
