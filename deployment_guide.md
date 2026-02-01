# Guía de Despliegue Local - Sistema de Asistencia QR

Esta guía detalla los pasos necesarios para configurar y ejecutar el proyecto en un entorno local.

## 📋 Requisitos Previos

- **Python 3.10+**: Asegúrate de tener Python instalado.
- **Terminal/Línea de Comandos**: Para ejecutar los comandos.

## 🚀 Pasos de Instalación

### 1. Clonar el Repositorio (si aplica)
Si tienes el código en un archivo comprimido, extráelo. Si es un repositorio git:
```bash
git clone <url-del-repositorio>
cd qr_asistencia
```

### 2. Configurar el Entorno Virtual
Es recomendable usar un entorno virtual para aislar las dependencias.

**En macOS/Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**En Windows:**
```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Instalar Dependencias
Instala las librerías necesarias listadas en `requirements.txt`.
```bash
pip install -r requirements.txt
```
> [!NOTE]
> Si encuentras errores de codificación con `requirements.txt`, asegúrate de que el archivo esté en formato UTF-8.

### 4. Configurar la Base de Datos
Aplica las migraciones para crear las tablas necesarias en la base de datos SQLite.
```bash
python manage.py migrate
```

### 5. Crear un Superusuario (Administrador)
Para acceder al panel de administración de Django.
```bash
python manage.py createsuperuser
```
Sigue las instrucciones para establecer nombre de usuario, correo y contraseña.

### 6. Ejecutar el Servidor de Desarrollo
Inicia el servidor local.
```bash
python manage.py runserver
```

### 7. Construcción de la App Móvil (Flutter)
Si deseas generar la aplicación para instalarla en tu dispositivo.

**Requisitos:**
- Flutter SDK instalado y configurado.

**Pasos:**
1. Navega al directorio de la app móvil:
   ```bash
   cd asistencia_scanner_app
   ```
2. Instala las dependencias de Flutter:
   ```bash
   flutter pub get
   ```
3. Generar el APK (Android):
   ```bash
   flutter build apk --release
   ```
   *El archivo generado estará en: `build/app/outputs/flutter-apk/app-release.apk`*

4. Generar para iOS (Requiere Xcode / Mac):
   ```bash
   flutter build ios --release
   ```

## 🌐 Acceso a la Aplicación

Una vez que el servidor esté corriendo, abre tu navegador y visita:

- **Aplicación Principal**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Panel de Administración**: [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)

## 🛠 Solución de Problemas Comunes

- **Error de codificación en requirements.txt**:
  Ejecuta: `iconv -f UTF-16LE -t UTF-8 requirements.txt > requirements.utf8.txt && mv requirements.utf8.txt requirements.txt` (en Mac/Linux).
  
- **Puerto ocupado**:
  Si el puerto 8000 está en uso, ejecuta en otro puerto:
  ```bash
  python manage.py runserver 8080
  ```
