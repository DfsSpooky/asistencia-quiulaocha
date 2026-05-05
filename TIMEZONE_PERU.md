# Configuración de Zona Horaria - Perú (UTC-5)

## 🕐 Problema Resuelto
El sistema ahora está configurado correctamente para mostrar la hora de Perú (Lima, UTC-5).

## ✅ Cambios Aplicados

### 1. Django Settings
**Archivo**: `qr_asistencia/settings.py`

Ya estaba configurado correctamente:
```python
TIME_ZONE = 'America/Lima'  # Línea 137
USE_TZ = True               # Línea 141
LANGUAGE_CODE = 'es-us'     # Línea 135
```

### 2. Dockerfile
**Archivo**: `Dockerfile`

Se agregaron las siguientes configuraciones:

```dockerfile
# Variable de entorno para zona horaria
ENV TZ=America/Lima

# Paquete tzdata y configuración del sistema
RUN apt-get update && apt-get install -y \
    # ... otras dependencias ...
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*
```

### 3. Templates
Los templates ya cargan el módulo de timezone correctamente:
```django
{% load tz %}
```

Y usan formatos de fecha/hora apropiados.

## 🚀 Instrucciones de Despliegue

Para aplicar estos cambios en el servidor VPS:

### En tu máquina local:

```bash
cd /Users/miguel/Documents/GitHub/asistencia-quiulaocha

# Hacer commit de los cambios
git add Dockerfile
git commit -m "feat: configurar zona horaria de Perú (America/Lima) en Docker"
git push
```

### En el VPS (SSH):

```bash
# Conectarse al VPS
ssh quiulacocha@vps-fab92c9a

# Ir al directorio del proyecto
cd ~/web/quiulacocha.theworkpc.com/public_html

# Obtener los últimos cambios
git pull

# Reconstruir el contenedor con la nueva configuración
docker compose down
docker compose up -d --build

# Verificar que el contenedor está corriendo
docker compose ps

# Ver logs si hay algún problema
docker compose logs -f web
```

### Verificación:

Para verificar que la zona horaria se aplicó correctamente:

```bash
# Verificar la hora dentro del contenedor
docker compose exec web date

# Debería mostrar algo como:
# Sun Feb  2 17:12:00 -05 2026
```

Para verificar en la aplicación:
1. Ir a la sección de "Historial de Asistencias"
2. Las horas de ingreso/salida deben mostrar la hora de Perú
3. Los reportes PDF deben mostrar la hora correcta en "Reporte generado el..."

## 🔍 Cómo Funciona

### Django + Timezone
Django almacena todas las fechas en UTC en la base de datos cuando `USE_TZ = True`, pero las convierte automáticamente a la zona horaria especificada en `TIME_ZONE` al mostrarlas.

### Contenedor Docker
El contenedor Docker necesita conocer la zona horaria del sistema para que los comandos como `date` y los procesos del sistema muestren la hora correcta.

### Variables de Entorno
- `TZ=America/Lima`: Indica al sistema operativo la zona horaria
- `tzdata`: Paquete que contiene la base de datos de zonas horarias
- `/etc/localtime`: Link simbólico que apunta a la zona horaria actual
- `/etc/timezone`: Archivo que contiene el nombre de la zona horaria

## 📝 Notas Importantes

1. **No cambiar `USE_TZ = False`**: Esto deshabilitaría el soporte de zonas horarias y causaría problemas con fechas/horas.

2. **Templates deben usar `{% load tz %}`**: Ya está implementado en todos los templates críticos.

3. **Formato de hora en Perú**: 
   - Formato 24 horas: `17:30`
   - Formato de fecha: `02/02/2026`

4. **Horario de Verano**: Perú NO usa horario de verano, por lo que `America/Lima` siempre es UTC-5.

## ✨ Funcionalidades Afectadas

Las siguientes secciones ahora mostrarán la hora correcta de Perú:

- ✅ **Historial de Asistencias**: Horas de ingreso/salida
- ✅ **Reportes PDF**: Fecha de generación y registros
- ✅ **Reportes Excel/CSV**: Timestamps
- ✅ **Logs del Sistema**: Registro de acciones
- ✅ **Notificaciones**: Timestamps de eventos
- ✅ **Dashboard**: Estadísticas por fecha

## 🐛 Troubleshooting

### Si las horas aún se muestran incorrectas:

1. **Verificar zona horaria del contenedor**:
   ```bash
   docker compose exec web date
   docker compose exec web cat /etc/timezone
   ```

2. **Verificar settings de Django**:
   ```bash
   docker compose exec web python manage.py shell
   >>> from django.conf import settings
   >>> print(settings.TIME_ZONE)
   >>> print(settings.USE_TZ)
   ```

3. **Limpiar caché del navegador**: Ctrl + Shift + R

4. **Reiniciar contenedor**:
   ```bash
   docker compose restart web
   ```

## 📚 Referencias

- [Django Time Zones](https://docs.djangoproject.com/en/5.0/topics/i18n/timezones/)
- [Python timezone database](https://docs.python.org/3/library/zoneinfo.html)
- [Docker timezone configuration](https://docs.docker.com/config/containers/resource_constraints/)

---

**Cambios aplicados**: `2026-02-02`
**Zona Horaria**: `America/Lima (UTC-5)`
**Sin Horario de Verano**: Siempre UTC-5 durante todo el año
