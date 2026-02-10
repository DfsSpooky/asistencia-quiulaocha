
# 🚀 Despliegue en Hestia CP - Instrucciones Rápidas

Hemos actualizado el proceso para usar la carpeta `public_html` que ya existe.

## 1. Subir Archivos al Servidor
Sube todo el contenido de tu proyecto **dentro de `public_html`**.

**Ruta Final:**
`/home/quiulacocha/web/quiulacocha.theworkpc.com/public_html`

Asegúrate de subir:
- `deploy_server.sh`
- `docker-compose.yml`
- `Dockerfile`
- `requirements.txt`
- Carpetas del código (`asistencia`, `qr_asistencia`, etc.)

> **Nota:** Si ves archivos como `index.html` o `robots.txt` que ya estaban ahí, puedes borrarlos o dejar que se sobrescriban.

## 2. Ejecutar el Script de Despliegue
Conéctate por SSH al servidor y ejecuta:

```bash
# Navegar a la carpeta
cd /home/quiulacocha/web/quiulacocha.theworkpc.com/public_html

# Dar permisos de ejecución
chmod +x deploy_server.sh

# Ejecutar el despliegue
./deploy_server.sh
```

Este script:
1. Creará el archivo `.env` si no existe.
2. Construirá los contenedores Docker.
3. Ejecutará las migraciones.
4. Colectará los archivos estáticos.

**IMPORTANTE:** Después de la primera ejecución, edita el archivo `.env` generado para poner tus contraseñas reales de base de datos y Secret Key.
```bash
nano .env
```
Y reinicia:
```bash
docker compose restart
```

## 3. Configurar Nginx (Hestia CP)
Para que la web cargue correctamente en el puerto 80/443:

### Opción A: Usar Plantillas Personalizadas (Si tienes root)
Si tienes acceso root, copia las plantillas actualizadas:
```bash
cp nginx_hestia_templates/django-8000.tpl /usr/local/hestia/data/templates/web/nginx/proxy/
cp nginx_hestia_templates/django-8000.stpl /usr/local/hestia/data/templates/web/nginx/proxy/
```
Luego en Hestia Panel -> Web -> Tu Dominio -> Proxy Template -> **django-8000**.

### Opción B: Configuración Manual (Sin root)
Si no eres root, Docker ya está escuchando en el puerto 8000.
Solo asegúrate de que en Hestia, la plantilla proxy sea **default**.
Puedes editar la configuración Nginx del dominio si sabes cómo, pero generalmente con **default** y Docker corriendo, podrías necesitar un pequeño ajuste en Hestia para que no sirva los archivos estáticos por defecto de Nginx si no los encuentra.
