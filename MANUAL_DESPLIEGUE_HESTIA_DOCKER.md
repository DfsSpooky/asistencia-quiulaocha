# Manual De Despliegue En Hestia Con Docker

Este manual esta preparado para tu caso real:

- VPS con usuario inicial: `debian`
- Usuario Hestia: `aquiulacocha`
- Dominio de produccion: `quiulacocha.theworkpc.com`
- Repositorio: `https://github.com/DfsSpooky/asistencia-quiulaocha.git`
- Rama: `despliegue-local-y-produccion`

La idea es que el despliegue sea simple:

1. Entrar por SSH como `debian`
2. Pasar a `root`
3. Crear el usuario y dominio en Hestia
4. Clonar el repo en una carpeta segura
5. Ejecutar `deploy_server.sh`
6. Seleccionar la plantilla `django-8000` en Hestia

## Como queda la arquitectura

Hestia hace de proxy con Nginx y Docker corre la app:

- Hestia/Nginx recibe el trafico web
- `/static/` sale desde `public_html/static/`
- `/media/` sale desde `private/media/`
- Django corre en Docker por `127.0.0.1:8000`
- PostgreSQL corre en Docker

La estructura final del sitio sera:

```text
/home/aquiulacocha/web/quiulacocha.theworkpc.com/
  app/
  private/
    .env
    media/
  public_html/
    static/
```

## Paso 1. Entrar al VPS

```bash
ssh debian@IP_DE_TU_VPS
```

Luego:

```bash
sudo su -
whoami
```

Debe responder:

```bash
root
```

## Paso 2. Verificar dependencias

```bash
docker --version
docker compose version
git --version
nginx -v
```

Si falta Git:

```bash
apt update
apt install -y git curl
```

## Paso 3. Crear el usuario en Hestia

Si todavia no existe el usuario `aquiulacocha`, puedes crearlo desde el panel o por comando.

Ejemplo por comando:

```bash
v-add-user aquiulacocha CLAVE_SEGURA correo@tu-dominio.com default
```

Si ya existe, verifica:

```bash
v-list-users
```

## Paso 4. Crear el dominio en Hestia

Si todavia no existe:

```bash
v-add-web-domain aquiulacocha quiulacocha.theworkpc.com
```

Si usaras SSL con Let's Encrypt:

```bash
v-add-letsencrypt-domain aquiulacocha quiulacocha.theworkpc.com
```

Verifica:

```bash
v-list-web-domains aquiulacocha
```

## Paso 5. Preparar rutas

```bash
export HESTIA_USER="aquiulacocha"
export DOMAIN="quiulacocha.theworkpc.com"
export SITE_ROOT="/home/$HESTIA_USER/web/$DOMAIN"
export APP_DIR="$SITE_ROOT/app"
export PRIVATE_DIR="$SITE_ROOT/private"
export PUBLIC_DIR="$SITE_ROOT/public_html"
```

Crear estructura:

```bash
mkdir -p "$APP_DIR" "$PRIVATE_DIR/media" "$PUBLIC_DIR/static"
chown -R "$HESTIA_USER:$HESTIA_USER" "$SITE_ROOT"
```

Verifica:

```bash
find "$SITE_ROOT" -maxdepth 2 -type d | sort
```

## Paso 6. Clonar el proyecto

```bash
cd "$APP_DIR"
git clone --branch despliegue-local-y-produccion https://github.com/DfsSpooky/asistencia-quiulaocha.git .
```

Verifica:

```bash
git branch --show-current
git remote -v
```

Debes ver:

- branch `despliegue-local-y-produccion`
- remoto `DfsSpooky/asistencia-quiulaocha`

## Paso 7. Crear el .env seguro

```bash
cp .env.example "$PRIVATE_DIR/.env"
nano "$PRIVATE_DIR/.env"
```

Contenido recomendado:

```env
DEBUG=False
SECRET_KEY=CAMBIA_ESTO_POR_UNA_CLAVE_LARGA_Y_ALEATORIA
ALLOWED_HOSTS=quiulacocha.theworkpc.com,www.quiulacocha.theworkpc.com,localhost,127.0.0.1
EXTRA_ALLOWED_HOSTS=
CSRF_TRUSTED_ORIGINS=https://quiulacocha.theworkpc.com,https://www.quiulacocha.theworkpc.com
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True

POSTGRES_DB=asistencia_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=CAMBIA_ESTO_POR_UNA_PASSWORD_SEGURA
POSTGRES_HOST=db
POSTGRES_PORT=5432
```

Generar `SECRET_KEY`:

```bash
openssl rand -hex 32
```

Dar permisos:

```bash
chown aquiulacocha:aquiulacocha "$PRIVATE_DIR/.env"
chmod 600 "$PRIVATE_DIR/.env"
```

## Paso 8. Instalar la plantilla de Hestia

```bash
cd "$APP_DIR"
cp nginx_hestia_templates/django-8000.tpl /usr/local/hestia/data/templates/web/nginx/proxy/
cp nginx_hestia_templates/django-8000.stpl /usr/local/hestia/data/templates/web/nginx/proxy/
```

Verifica:

```bash
ls -l /usr/local/hestia/data/templates/web/nginx/proxy/django-8000.*
```

## Paso 9. Ejecutar el despliegue rapido con el .sh

Este es el paso principal:

```bash
cd "$APP_DIR"
chmod +x deploy_server.sh
chmod +x scripts/start-web.sh
./deploy_server.sh
```

Ese script hace esto:

1. Usa el usuario Hestia `aquiulacocha`
2. Usa el dominio `quiulacocha.theworkpc.com`
3. Clona o actualiza el repo correcto
4. Lee el `.env` desde `private/.env`
5. Levanta Docker
6. Ejecuta migraciones
7. Ejecuta `collectstatic`
8. Deja estaticos en `public_html/static`
9. Instala plantillas de Hestia

## Paso 10. Seleccionar el Proxy Template en Hestia

En Hestia Panel:

1. Ve a `WEB`
2. Edita `quiulacocha.theworkpc.com`
3. En `Proxy Template` selecciona `django-8000`
4. Guarda

Luego reconstruye por seguridad:

```bash
v-rebuild-web-domain aquiulacocha quiulacocha.theworkpc.com
systemctl reload nginx
```

## Paso 11. Verificar contenedores

```bash
cd "$APP_DIR"
docker compose ps
docker compose logs --tail=100 web
docker compose logs --tail=100 db
```

## Paso 12. Verificar la web

Pruebas locales:

```bash
curl -I http://127.0.0.1:8000
```

Pruebas publicas:

```bash
curl -I https://quiulacocha.theworkpc.com
curl -I https://quiulacocha.theworkpc.com/static/css/custom.css
```

## Paso 13. Verificaciones de seguridad

Confirma:

```bash
ls -la "$PUBLIC_DIR"
ls -la "$PRIVATE_DIR"
ss -ltnp | grep 8000
```

Debe cumplirse:

- `public_html` no tiene `.env`
- `public_html` no tiene codigo Python
- `private/.env` existe
- el puerto 8000 escucha en `127.0.0.1`

## Paso 14. Comandos utiles de mantenimiento

Reiniciar:

```bash
cd "$APP_DIR"
docker compose restart
```

Actualizar:

```bash
cd "$APP_DIR"
git fetch origin
git checkout despliegue-local-y-produccion
git pull origin despliegue-local-y-produccion
docker compose up -d --build
docker compose exec web python manage.py migrate --noinput
docker compose exec web python manage.py collectstatic --noinput
```

Entrar al contenedor:

```bash
cd "$APP_DIR"
docker compose exec web sh
```

## Paso 15. Solucion de errores comunes

`502 Bad Gateway`

```bash
systemctl status docker
cd "$APP_DIR"
docker compose ps
docker compose logs --tail=100 web
```

`DisallowedHost`

Revisa `ALLOWED_HOSTS` en:

```bash
nano "$PRIVATE_DIR/.env"
```

Luego:

```bash
cd "$APP_DIR"
docker compose down
docker compose up -d --build
```

`403` o no carga por Hestia

```bash
v-rebuild-web-domain aquiulacocha quiulacocha.theworkpc.com
systemctl reload nginx
```

## Resumen ultra rapido

```bash
ssh debian@IP_DE_TU_VPS
sudo su -
export HESTIA_USER="aquiulacocha"
export DOMAIN="quiulacocha.theworkpc.com"
export SITE_ROOT="/home/$HESTIA_USER/web/$DOMAIN"
export APP_DIR="$SITE_ROOT/app"
export PRIVATE_DIR="$SITE_ROOT/private"
export PUBLIC_DIR="$SITE_ROOT/public_html"
mkdir -p "$APP_DIR" "$PRIVATE_DIR/media" "$PUBLIC_DIR/static"
cd "$APP_DIR"
git clone --branch despliegue-local-y-produccion https://github.com/DfsSpooky/asistencia-quiulaocha.git .
cp .env.example "$PRIVATE_DIR/.env"
nano "$PRIVATE_DIR/.env"
chmod 600 "$PRIVATE_DIR/.env"
cp nginx_hestia_templates/django-8000.tpl /usr/local/hestia/data/templates/web/nginx/proxy/
cp nginx_hestia_templates/django-8000.stpl /usr/local/hestia/data/templates/web/nginx/proxy/
chmod +x deploy_server.sh scripts/start-web.sh
./deploy_server.sh
v-rebuild-web-domain aquiulacocha quiulacocha.theworkpc.com
systemctl reload nginx
curl -I https://quiulacocha.theworkpc.com
```
