
# 🚀 Despliegue en Hestia CP - Guía Corregida

El problema de permisos ocurre porque estás logueado como un usuario (`debian`) pero los archivos pertenecen a otro (`quiulacocha` o `root`).
Para arreglar esto definitivamente, **debes usar `sudo` o ser `root`** para ejecutar el script. El script se encargará automáticamente de corregir los permisos para el usuario `quiulacocha`.

## 1. Subir Archivos al Servidor
Sube todo el contenido de tu proyecto **dentro de `public_html`**:
`/home/quiulacocha/web/quiulacocha.theworkpc.com/public_html`

## 2. Ejecutar el Script como Root
Conéctate por SSH y conviértete en superusuario (root) o usa sudo.

```bash
# Opción A: Convertirse en root (Recomendado)
sudo su -

# Navegar a la carpeta
cd /home/quiulacocha/web/quiulacocha.theworkpc.com/public_html

# Dar permisos al script (ahora eres root, así que funcionará)
chmod +x deploy_server.sh

# Ejecutar el despliegue
./deploy_server.sh
```

El script actualizado ahora hace esto automáticamente:
1.  Verifica que seas root (para poder usar Docker).
2.  Ejecuta `git pull` como usuario `quiulacocha` (para no romper permisos de git).
3.  Levanta los contenedores Docker.
4.  **IMPORTANTE:** Al final, ejecuta `chown -R quiulacocha:quiulacocha .` para asegurar que Hestia pueda leer todos los archivos.

## 3. Configurar Nginx (Hestia CP)
*(Igual que antes)*

Copia las plantillas como **root**:
```bash
cp nginx_hestia_templates/django-8000.tpl /usr/local/hestia/data/templates/web/nginx/proxy/
cp nginx_hestia_templates/django-8000.stpl /usr/local/hestia/data/templates/web/nginx/proxy/
```
En Hestia Panel -> Web -> quiulacocha.theworkpc.com -> Proxy Template -> Selecciona **django-8000**.
