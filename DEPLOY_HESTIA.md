
# 🚀 Guía Definitiva de Despliegue en Hestia CP (Docker)

Esta guía resume todo el proceso corregido y probado para desplegar la aplicación en `quiulacocha.theworkpc.com`.

## 1. Preparación del Servidor

Sube todos los archivos del proyecto a la carpeta:
`/home/quiulacocha/web/quiulacocha.theworkpc.com/public_html`

Asegúrate de incluir:
- `deploy_server.sh` (Script de automatización)
- `docker-compose.yml`
- `Dockerfile`
- `nginx_hestia_templates/` (Carpeta con las plantillas corregidas)
- `requirements.txt`
- Código fuente (`asistencia`, `qr_asistencia`, etc.)

## 2. Automatización (Script de Despliegue)

Hemos creado un script llamado `deploy_server.sh` que hace todo el trabajo sucio. 
**DEBE EJECUTARSE COMO ROOT** para tener permisos de Docker y Nginx.

```bash
# 1. Conéctate como root
ssh root@tu_ip (o sudo su -)

# 2. Ve a la carpeta
cd /home/quiulacocha/web/quiulacocha.theworkpc.com/public_html

# 3. Dale permisos y ejecuta
chmod +x deploy_server.sh
./deploy_server.sh
```

**¿Qué hace este script?**
1.  Verifica que seas root.
2.  Baja cambios de Git (usando el usuario `quiulacocha` para no romper permisos).
3.  Crea/Actualiza el `.env`.
4.  Levanta los contenedores Docker (reconstruye si es necesario).
5.  Ejecuta migraciones y colecta estáticos.
6.  **Instala las plantillas de Nginx** en `/usr/local/hestia/data/templates/web/nginx/`.
7.  Arregla los permisos de todos los archivos para que Hestia no se queje.

## 3. Configuración en Hestia CP (Solo la primera vez)

Una vez ejecutado el script, las plantillas Nginx estarán instaladas.

1.  Entra a tu Panel Hestia.
2.  Ve a **WEB** -> Editar `quiulacocha.theworkpc.com`.
3.  Busca la opción **Plantilla Proxy** (Proxy Template) o **Nginx Template**.
4.  Selecciona **`django-8000`**.
5.  Guardar.

Esto le dice a Hestia que tu web está en el puerto 8000 (Docker) y no en el sistema de archivos normal.

## 4. Mantenimiento y Actualizaciones

Para subir cambios futuros, simplemente:
1.  Haz `git push` desde tu máquina local.
2.  Entra al servidor y ejecuta el script nuevamente:
    ```bash
    cd /home/quiulacocha/web/quiulacocha.theworkpc.com/public_html
    ./deploy_server.sh
    ```

## Solución de Problemas Comunes

-   **Error 403 / Access Denied**: Significa que Hestia no está usando la plantilla `django-8000`. Revisa el paso 3.
-   **Address already in use (Nginx falla)**: Significa conflicto de puertos. Asegúrate de usar las plantillas `django-8000` que tienen `%proxy_port%` y no `%web_port%`.
-   **DisallowedHost**: Falta el dominio en `ALLOWED_HOSTS`. Revisa `settings.py`.
