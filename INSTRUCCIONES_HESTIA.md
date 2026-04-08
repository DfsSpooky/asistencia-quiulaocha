# Despliegue en Hestia CP - Flujo Seguro

La instruccion anterior de subir todo dentro de `public_html` ya no aplica. Desde ahora:

- `public_html` solo para estaticos.
- `app/` para el codigo fuente.
- `private/` para `.env` y `media/`.

## 1. Estructura esperada

```bash
/home/quiulacocha/web/quiulacocha.theworkpc.com/
├── app/
├── private/
│   ├── .env
│   └── media/
└── public_html/
    └── static/
```

## 2. Ejecutar el script como root

```bash
sudo su -
mkdir -p /home/quiulacocha/web/quiulacocha.theworkpc.com/app
cd /home/quiulacocha/web/quiulacocha.theworkpc.com/app
chmod +x deploy_server.sh
./deploy_server.sh
```

## 3. Que cambia respecto al plan viejo

1. El repo ya no vive dentro de `public_html`.
2. El `.env` queda en `private/.env`.
3. Los uploads quedan en `private/media/`.
4. Solo `collectstatic` escribe en `public_html/static/`.
5. Hestia publica estaticos y proxya el backend en `127.0.0.1:8000`.

## 4. Configurar Nginx en Hestia

Como `root`:

```bash
cp /home/quiulacocha/web/quiulacocha.theworkpc.com/app/nginx_hestia_templates/django-8000.tpl /usr/local/hestia/data/templates/web/nginx/proxy/
cp /home/quiulacocha/web/quiulacocha.theworkpc.com/app/nginx_hestia_templates/django-8000.stpl /usr/local/hestia/data/templates/web/nginx/proxy/
```

Luego en Hestia Panel:

1. `Web`
2. `quiulacocha.theworkpc.com`
3. `Proxy Template`
4. Seleccionar `django-8000`

## 5. Checklist rapido

- `public_html` no contiene codigo ni secretos.
- `private/.env` tiene permisos `600`.
- `DEBUG=False`.
- Gunicorn escucha solo en `127.0.0.1:8000`.
- `/static/` sale por Nginx.
