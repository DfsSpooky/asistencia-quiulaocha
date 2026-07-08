# Manual Rapido Hestia + Docker

Este manual es la version corta para dejar el sistema desplegado rapido en:

- VPS: acceso inicial con `debian`
- Usuario Hestia: `quiulacocha`
- Dominio: `ccquiulacocha.com`
- Repo: `https://github.com/DfsSpooky/asistencia-quiulaocha.git`
- Rama: `Implementacion-carnets-despliegue`

## 1. Entrar al VPS

```bash
ssh debian@IP_DE_TU_VPS
sudo su -
```

## 2. Crear lo basico en Hestia

Si el usuario `quiulacocha` no existe:

```bash
v-add-user quiulacocha TU_PASSWORD correo@tucorreo.com default
```

Si el dominio no existe:

```bash
v-add-web-domain quiulacocha ccquiulacocha.com
v-add-letsencrypt-domain quiulacocha ccquiulacocha.com
```

Verificar:

```bash
v-list-users
v-list-web-domains quiulacocha
```

## 3. Clonar el repo

```bash
mkdir -p /home/quiulacocha/web/ccquiulacocha.com/app
cd /home/quiulacocha/web/ccquiulacocha.com/app
git clone --branch Implementacion-carnets-despliegue https://github.com/DfsSpooky/asistencia-quiulaocha.git .
```

## 4. Ejecutar el despliegue

```bash
chmod +x deploy_server.sh
chmod +x scripts/start-web.sh
./deploy_server.sh
```

## 5. Activar la plantilla en Hestia

En Hestia Panel:

1. Ve a `WEB`
2. Edita `ccquiulacocha.com`
3. En `Proxy Template` selecciona `django-8020`
4. Guarda

Luego en consola:

```bash
v-rebuild-web-domain quiulacocha ccquiulacocha.com
systemctl reload nginx
```

## 6. Revisar que levanto bien

```bash
cd /home/quiulacocha/web/ccquiulacocha.com/app
docker compose ps
docker compose logs --tail=100 web
curl -I https://ccquiulacocha.com
```

## 7. Importante antes de produccion real

El script crea automaticamente este archivo si no existe:

```bash
/home/quiulacocha/web/ccquiulacocha.com/private/.env
```

Debes editarlo y cambiar como minimo:

- `SECRET_KEY`
- `POSTGRES_PASSWORD`

Comando:

```bash
nano /home/quiulacocha/web/ccquiulacocha.com/private/.env
```

Luego reinicia:

```bash
cd /home/quiulacocha/web/ccquiulacocha.com/app
docker compose up -d --build
```

## 8. Resumen ultra corto

```bash
ssh debian@IP_DE_TU_VPS
sudo su -
v-add-user quiulacocha TU_PASSWORD correo@tucorreo.com default
v-add-web-domain quiulacocha ccquiulacocha.com
v-add-letsencrypt-domain quiulacocha ccquiulacocha.com
mkdir -p /home/quiulacocha/web/ccquiulacocha.com/app
cd /home/quiulacocha/web/ccquiulacocha.com/app
git clone --branch Implementacion-carnets-despliegue https://github.com/DfsSpooky/asistencia-quiulaocha.git .
chmod +x deploy_server.sh scripts/start-web.sh
./deploy_server.sh
v-rebuild-web-domain quiulacocha ccquiulacocha.com
systemctl reload nginx
```
