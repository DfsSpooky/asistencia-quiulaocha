# Guia de Despliegue en Hestia CP

Esta guia resume el flujo recomendado para desplegar la aplicacion en `quiulacocha.theworkpc.com` usando Docker y Hestia.

## Archivos clave

Debes tener en el servidor:

- `deploy_server.sh`
- `docker-compose.yml`
- `Dockerfile`
- `scripts/start-web.sh`
- `nginx_hestia_templates/`
- `requirements.txt`
- el codigo fuente del proyecto

## Despliegue

Ejecuta como `root`:

```bash
cd /home/quiulacocha/web/quiulacocha.theworkpc.com/public_html
chmod +x deploy_server.sh
./deploy_server.sh
```

## Que hace el script

1. Verifica permisos de root.
2. Actualiza el repositorio.
3. Asegura la existencia de `.env`.
4. Levanta los contenedores con `docker-compose.yml`.
5. Ejecuta `python manage.py check` dentro del contenedor `web`.
6. Instala las plantillas Nginx de Hestia.
7. Corrige ownership final.

## Arranque del contenedor web

El servicio `web` usa `scripts/start-web.sh`, que hace esto automaticamente:

1. Espera PostgreSQL si el entorno usa Postgres.
2. Ejecuta `python manage.py migrate --noinput`.
3. Ejecuta `python manage.py collectstatic --noinput`.
4. Inicia Gunicorn.

Con eso, reinicios y despliegues manuales son mucho mas robustos.

## Configuracion en Hestia

En Hestia:

1. Ve a `WEB`.
2. Edita `quiulacocha.theworkpc.com`.
3. En Proxy Template selecciona `django-8000`.
4. Guarda.

## Problemas comunes

- `403 / Access Denied`: Hestia no esta usando la plantilla `django-8000`.
- `DisallowedHost`: el dominio no esta incluido en `ALLOWED_HOSTS`.
- `PostgreSQL password authentication failed`: el `.env` no coincide con las credenciales con las que fue creado el volumen de Postgres. No recrees el volumen sin backup previo.
