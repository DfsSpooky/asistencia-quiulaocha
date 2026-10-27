# Guia de Despliegue Seguro en Hestia CP

Esta guia reemplaza el plan anterior. En Hestia, `public_html` debe contener solo archivos estaticos. El codigo de Django, `.env`, media y runtime quedan fuera del docroot.

## Layout recomendado

```text
/home/quiulacocha/web/ccquiulacocha.com/
  app/                 <- repositorio git y docker-compose
  private/
    .env               <- secretos
    media/             <- uploads, QR, logos, reportes
  public_html/
    static/            <- salida de collectstatic
```

## Principios de seguridad

1. `public_html` no debe contener codigo fuente, `.git`, `.env`, base de datos ni backups.
2. Gunicorn escucha solo en `127.0.0.1:8020`.
3. Nginx de Hestia publica `/static/` desde `public_html/static/` y enruta `/` al backend.
4. `media/` vive fuera de `public_html`, en `private/media/`.
5. Django no debe servir `static` ni `media` con `DEBUG=False`.

## Despliegue

Ejecuta como `root`:

```bash
mkdir -p /home/quiulacocha/web/ccquiulacocha.com/app
cd /home/quiulacocha/web/ccquiulacocha.com/app
chmod +x deploy_server.sh
./deploy_server.sh
```

## Que hace el script

1. Crea `app/`, `private/media/` y `public_html/static/`.
2. Clona o actualiza el repo en `app/`.
3. Crea `private/.env` con permisos `600`.
4. Levanta Docker usando ese `.env` fuera del docroot.
5. Ejecuta migraciones y `collectstatic`; los estaticos terminan en `public_html/static/`.
6. Instala la plantilla `django-8020` actualizada para Hestia.
7. Ajusta permisos para que lo publico y lo privado queden separados.

## Configuracion en Hestia

En Hestia:

1. Ve a `WEB`.
2. Edita `ccquiulacocha.com`.
3. Mantén el docroot en `public_html`.
4. En Proxy Template selecciona `django-8020`.
5. Guarda y reconstruye la configuracion del dominio si hace falta.

## Validaciones previas al go-live

1. Confirma que `DEBUG=False`.
2. Confirma que `SECRET_KEY` sea larga y aleatoria.
3. Configura y conserva `QR_SIGNING_KEY` estable entre despliegues/migraciones. Si rota, los QR antiguos quedarán inválidos.
4. Si rotaste clave por error, usa `QR_SIGNING_FALLBACK_KEYS` (lista separada por comas) para aceptar QR firmados con claves anteriores.
5. Confirma que `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` contengan solo dominios reales.
4. Verifica que `public_html/` no tenga `.env`, `.git`, `db.sqlite3`, backups ni codigo Python.
5. Verifica que `https://dominio/static/...` responda por Nginx y que `https://dominio/` responda por proxy a Gunicorn.

## Problemas comunes

- `403 / Access Denied`: Hestia no esta usando la plantilla `django-8020`.
- `DisallowedHost`: el dominio no esta incluido en `ALLOWED_HOSTS`.
- `502 Bad Gateway`: Gunicorn no esta arriba o Docker no pudo iniciar el servicio `web`.
- `media` no carga: revisa que `private/media/` exista y que la plantilla Nginx apunte a esa ruta.
