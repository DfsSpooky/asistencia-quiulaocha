# Manual de Despliegue Local - Sistema de Asistencia

Este manual te guiará paso a paso para levantar el ecosistema completo del proyecto (Backend Django + App Móvil Flutter) en tu entorno de desarrollo local.

---

## 📋 Tabla de Contenidos

1. [Prerrequisitos del Sistema](#-prerrequisitos-del-sistema)
2. [Parte 1: Backend Django](#-parte-1-backend-django)
3. [Parte 2: Conectividad (El Puente)](#-parte-2-conectividad-el-puente)
4. [Parte 3: Aplicación Móvil Flutter](#-parte-3-aplicación-móvil-flutter)
5. [Verificación Final](#-verificación-final)
6. [Solución de Problemas](#-solución-de-problemas)

---

## 🔧 Prerrequisitos del Sistema

### Software Necesario

| Software | Versión Mínima | Propósito | Instalación |
|----------|---------------|-----------|-------------|
| **Python** | 3.10+ | Backend Django | [python.org](https://www.python.org/downloads/) |
| **Git** | 2.x | Control de versiones | [git-scm.com](https://git-scm.com/) |
| **Flutter SDK** | 3.10.7+ | Framework móvil | [flutter.dev](https://docs.flutter.dev/get-started/install) |
| **Android Studio** | Última | Emulador/compilación Android | [developer.android.com](https://developer.android.com/studio) |
| **Xcode** (solo macOS) | 15+ | Compilación iOS | App Store |
| **VS Code** | Última | Editor recomendado | [code.visualstudio.com](https://code.visualstudio.com/) |

### Base de Datos

Este proyecto usa **SQLite** por defecto (incluido con Python), perfecto para desarrollo local. No requiere instalación adicional.

### Extensiones Recomendadas (VS Code)

```bash
# Python
code --install-extension ms-python.python
code --install-extension ms-python.vscode-pylance

# Flutter/Dart
code --install-extension Dart-Code.dart-code
code --install-extension Dart-Code.flutter

# Utilidades
code --install-extension esbenp.prettier-vscode
code --install-extension ms-vscode.vscode-json
```

---

## 🐍 Parte 1: Backend Django

### Paso 1: Clonar el Repositorio

```bash
# Navega a tu directorio de proyectos
cd ~/Documents/Projects

# Clona el repositorio
git clone https://github.com/tu-usuario/asistencia-quiulaocha.git
cd asistencia-quiulaocha
```

### Paso 2: Crear y Activar Entorno Virtual

**macOS/Linux:**
```bash
# Crear entorno virtual
python3 -m venv venv

# Activar entorno virtual
source venv/bin/activate
```

**Windows:**
```bash
# Crear entorno virtual
python -m venv venv

# Activar entorno virtual
venv\Scripts\activate
```

> ✅ Si ves `(venv)` al inicio de tu terminal, el entorno está activo.

### Paso 3: Instalar Dependencias

```bash
# Actualizar pip
pip install --upgrade pip

# Instalar todas las dependencias del proyecto
pip install -r requirements.txt
```

**Dependencias principales instaladas:**
- Django 5.2
- Django REST Framework 3.16.0
- django-widget-tweaks 1.5.0
- Pillow 11.2.1 (procesamiento de imágenes)
- WeasyPrint 65.1 (generación de PDFs)
- qrcode 8.1 (generación de códigos QR)
- python-dotenv 1.1.0 (variables de entorno)

### Paso 4: Configurar Variables de Entorno

Crea un archivo `.env` en la raíz del proyecto:

```bash
# Crear archivo .env
touch .env

# Editar con tu editor preferido
code .env
```

**Contenido mínimo del archivo `.env`:**
```env
# Django Settings
SECRET_KEY=tu-clave-secreta-aqui-cambiar-en-produccion
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0,*

# Database (SQLite es el default, no requiere configuración adicional)
# Si usas PostgreSQL, descomenta y configura:
# DB_NAME=asistencia_db
# DB_USER=postgres
# DB_PASSWORD=tu_password
# DB_HOST=localhost
# DB_PORT=5432

# Media Files
MEDIA_ROOT=media/
MEDIA_URL=/media/
```

> 🔒 **Importante**: Nunca subas el archivo `.env` a Git. Ya está en `.gitignore`.

### Paso 5: Configurar la Base de Datos

```bash
# Aplicar migraciones
python manage.py migrate

# Crear superusuario para el admin de Django
python manage.py createsuperuser

# Sigue las instrucciones en pantalla:
# - Username: admin  (o tu preferencia)
# - Email: tu@email.com
# - Password: (elige una contraseña segura)
```

### Paso 6: (Opcional) Crear Datos de Prueba

```bash
# Si tienes fixtures o datos de prueba
python manage.py loaddata initial_data.json

# O crea manualmente desde el admin de Django (http://localhost:8000/admin)
```

### Paso 7: 🚀 Ejecutar el Servidor (CRÍTICO)

**Para desarrollo local con acceso desde la red:**

```bash
# Ejecutar servidor en TODAS las interfaces de red
python manage.py runserver 0.0.0.0:8000
```

> ⚠️ **IMPORTANTE**: Usa `0.0.0.0:8000` en lugar de solo `runserver` para que la app móvil pueda conectarse desde tu dispositivo/emulador.

El servidor estará disponible en:
- **Localhost**: http://localhost:8000
- **Red local**: http://TU_IP_LOCAL:8000

---

## 🌐 Parte 2: Conectividad (El Puente)

Esta es la parte crítica que conecta tu backend con la app móvil.

### Paso 1: Encontrar tu IP Local

**macOS/Linux:**
```bash
ifconfig | grep "inet " | grep -v 127.0.0.1

# O más específico:
ipconfig getifaddr en0  # WiFi en Mac
```

**Windows:**
```bash
ipconfig

# Busca "IPv4 Address" en tu adaptador de red activo
```

**Alternativas:**
- Ve a Configuración > Red > Detalles
- Usa `hostname -I` en Linux

**Ejemplo de salida:**
```
192.168.1.100
```

> 📝 **Anota esta IP**, la necesitarás en el siguiente paso.

### Paso 2: Configurar la URL Base en Flutter

**Archivo a modificar:** `asistencia_scanner_app/lib/api_service.dart`

```bash
# Abrir el archivo
code asistencia_scanner_app/lib/api_service.dart
```

**Busca la línea 9:**
```dart
static const String baseUrl = "https://oversophisticated-dedra-overgross.ngrok-free.dev";
```

**Cámbiala por tu IP local:**
```dart
static const String baseUrl = "http://192.168.1.100:8000";  // Reemplaza con TU IP
```

> ⚠️ **Nota**: Usa `http://` (no `https://`) para desarrollo local, a menos que configures SSL.

### Paso 3: Verificar Firewall

Asegúrate de que el puerto 8000 no esté bloqueado:

**macOS:**
```bash
# Verifica si el firewall está activo
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate

# Si está activo, permite Python
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/bin/python3
```

**Windows:**
- Panel de Control > Sistema y Seguridad > Firewall de Windows
- Permitir una aplicación > Python

**Linux:**
```bash
# Ubuntu/Debian
sudo ufw allow 8000/tcp
```

---

## 📱 Parte 3: Aplicación Móvil Flutter

### Paso 1: Verificar Instalación de Flutter

```bash
# Verifica que Flutter esté instalado correctamente
flutter doctor

# Debe mostrar checkmarks ✓ en:
# - Flutter (Channel stable)
# - Android toolchain
# - Xcode (solo macOS)
# - VS Code
```

**Si hay problemas, ejecuta:**
```bash
flutter doctor --android-licenses  # Acepta las licencias de Android
```

### Paso 2: Navegar al Proyecto Flutter

```bash
cd asistencia_scanner_app
```

### Paso 3: Instalar Dependencias

```bash
# Descargar todas las dependencias de pubspec.yaml
flutter pub get
```

**Dependencias principales:**
- `http: ^1.6.0` - Llamadas HTTP
- `mobile_scanner: ^7.1.4` - Escaneo de códigos QR
- `shared_preferences: ^2.5.4` - Almacenamiento local
- `flutter_secure_storage: ^10.0.0` - Almacenamiento seguro de tokens
- `google_fonts: ^8.0.0` - Tipografías
- `vibration: ^2.0.1` - Vibración háptica
- `audioplayers: ^6.1.0` - Sonidos de feedback

### Paso 4: Configurar Emulador/Dispositivo

**Opción A: Emulador Android**
```bash
# Listar emuladores disponibles
flutter emulators

# Crear uno si no existe
flutter emulators --create

# Iniciar emulador
flutter emulators --launch <nombre_emulador>

# O desde Android Studio:
# Tools > AVD Manager > Crear o iniciar un dispositivo virtual
```

**Opción B: Dispositivo Físico Android**
1. Habilita "Opciones de Desarrollador" en tu Android
2. Activa "Depuración USB"
3. Conecta el dispositivo por USB
4. Autoriza la conexión en el dispositivo

**Verificar dispositivos conectados:**
```bash
flutter devices
```

### Paso 5: Configurar Permisos (Crítico para QR)

**Android** - Ya configurado en `android/app/src/main/AndroidManifest.xml`:
```xml
<uses-permission android:name="android.permission.CAMERA" />
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.VIBRATE" />
```

**iOS** - Verifica `ios/Runner/Info.plist`:
```xml
<key>NSCameraUsageDescription</key>
<string>Se necesita acceso a la cámara para escanear códigos QR</string>
```

### Paso 6: 🚀 Ejecutar la Aplicación

```bash
# Modo debug (recomendado para desarrollo)
flutter run

# O especifica un dispositivo
flutter run -d <device_id>

# Para ver logs en tiempo real
flutter run --verbose
```

**Atajos útiles durante la ejecución:**
- `r` - Hot reload (recarga rápida)
- `R` - Hot restart (reinicio completo)
- `q` - Quit (salir)
- `p` - Toggle performance overlay

### Paso 7: Construir para Release (Opcional)

```bash
# Android APK
flutter build apk --release

# Android App Bundle (para Play Store)
flutter build appbundle --release

# iOS (solo macOS)
flutter build ios --release
```

---

## ✅ Verificación Final

### Lista de Chequeo

- [ ] **Backend Django corriendo**
  ```bash
  # Abre http://localhost:8000/admin en tu navegador
  # Debes ver la página de login del admin
  ```

- [ ] **Superusuario creado**
  ```bash
  # Inicia sesión en http://localhost:8000/admin
  # Usuario/contraseña que creaste con createsuperuser
  ```

- [ ] **API accesible desde la red**
  ```bash
  # Desde tu teléfono/emulador, abre el navegador
  # Visita: http://TU_IP_LOCAL:8000/admin
  # Debe cargar la página
  ```

- [ ] **App Flutter compilada**
  ```bash
  # La app debe iniciarse sin errores
  # Debes ver la pantalla de login
  ```

- [ ] **Conexión API exitosa**
  ```bash
  # En la app, intenta hacer login
  # Debe conectarse al backend y autenticarte
  ```

- [ ] **Cámara funcionando**
  ```bash
  # Navega a la sección de escaneo QR
  # La cámara debe activarse
  ```

- [ ] **Escaneo QR funcional**
  ```bash
  # Genera un QR de usuario desde el admin Django
  # Escanéalo con la app
  # Debe registrar la asistencia
  ```

### Prueba Completa End-to-End

1. **Backend**: Crea un evento activo desde el admin
2. **Backend**: Crea un usuario y descarga su QR
3. **App**: Haz login como escaneador
4. **App**: Selecciona el evento activo
5. **App**: Escanea el QR del usuario
6. **Verificar**: Revisa en el admin que la asistencia se registró

---

## 🔧 Solución de Problemas

### Backend Django

#### Error: `ModuleNotFoundError: No module named 'django'`
```bash
# Verifica que el entorno virtual esté activado
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate  # Windows

# Reinstala dependencias
pip install -r requirements.txt
```

#### Error: `(1146, "Table 'asistencia_usuario' doesn't exist")`
```bash
# Elimina la base de datos y recrea
rm  db.sqlite3
python manage.py migrate
python manage.py createsuperuser
```

#### El servidor no es accesible desde la red
```bash
# Verifica que uses 0.0.0.0:8000
python manage.py runserver 0.0.0.0:8000

# Verifica tu firewall
# Verifica que ALLOWED_HOSTS incluya '*' en .env
```

### Aplicación Flutter

#### Error: `Unable to connect to http://...`
```dart
// Verifica la URL en lib/api_service.dart
static const String baseUrl = "http://TU_IP:8000";  // Sin slash al final

// Verifica que el backend esté corriendo
// Verifica que estés en la misma red WiFi
```

#### Error: `Camera permission denied`
```bash
# Android: Desinstala y reinstala la app
flutter clean
flutter run

# Otorga permisos manualmente:
# Configuración > Apps > Asistencia Scanner > Permisos > Cámara
```

#### Error: `Handshake failed` (SSL/HTTPS)
```dart
// Para desarrollo local, usa HTTP (no HTTPS)
static const String baseUrl = "http://192.168.1.100:8000";

// Si necesitas HTTPS, configura un certificado autofirmado
```

#### Error: `MissingPluginException`
```bash
# Limpia y reconstruye
flutter clean
flutter pub get
flutter run
```

#### La app no se instala en Android
```bash
# Verifica la firma de la app
cd android
./gradlew signingReport

# Si hay problemas con Gradle
./gradlew clean
cd ..
flutter clean
flutter run
```

### Problemas de Red

#### No puedo conectarme desde el emulador
```dart
// Para emulador Android, usa 10.0.2.2 en lugar de localhost
static const String baseUrl = "http://10.0.2.2:8000";

// Para emulador iOS, localhost funciona
static const String baseUrl = "http://localhost:8000";

// Para dispositivo físico, usa tu IP local
static const String baseUrl = "http://192.168.1.100:8000";
```

#### Error: `Connection refused`
```bash
# Verifica que el backend esté corriendo
# Verifica el puerto (debe ser 8000)
# Verifica el firewall
# Ambos dispositivos deben estar en la misma red WiFi
```

---

## 📚 Recursos Adicionales

- **Django Docs**: https://docs.djangoproject.com/
- **Django REST Framework**: https://www.django-rest-framework.org/
- **Flutter Docs**: https://docs.flutter.dev/
- **Mobile Scanner**: https://pub.dev/packages/mobile_scanner

---

## 🎯 Próximos Pasos

Una vez que tengas todo funcionando:

1. **Personaliza** la configuración según tus necesidades
2. **Lee** el código para entender la arquitectura
3. **Experimenta** con las funcionalidades
4. **Desarrolla** nuevas features

---

**¿Problemas no listados aquí?** 

1. Revisa los logs de Django: terminal donde corre `runserver`
2. Revisa los logs de Flutter: terminal donde corre `flutter run`
3. Usa `flutter doctor -v` para diagnóstico detallado
4. Verifica la documentación de dependencias específicas

**¡Buena suerte con tu desarrollo! 🚀**
