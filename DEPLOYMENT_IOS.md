# Guía de Despliegue en iOS - Asistencia Scanner App

Esta guía detalla los pasos necesarios para configurar, compilar y desplegar la aplicación de scanner en dispositivos iOS utilizando macOS.

## 1. Requisitos Previos

### Xcode
Es obligatorio tener Xcode instalado para compilar aplicaciones de iOS.
- **Versión Recomendada**: Xcode 15.0 o superior.
- **Configuración**: Asegúrate de que Xcode esté seleccionado en el sistema:
  ```bash
  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
  sudo xcodebuild -license accept
  ```

### CocoaPods (Gestor de Dependencias)
Utiliza Homebrew para instalar CocoaPods de forma robusta:
```bash
brew install cocoapods
brew link --overwrite cocoapods
```

---

## 2. Configuración del Proyecto

Antes de compilar, asegúrate de que las dependencias estén al día:

1. **Obtener dependencias de Flutter**:
   ```bash
   flutter pub get
   ```

2. **Instalar Pods (Librerías nativas de iOS)**:
   ```bash
   cd ios
   pod install
   cd ..
   ```

---

## 3. Comandos de Compilación

### Compilación para Pruebas (Sin Firma)
Si solo quieres generar el archivo para verificar que todo compile correctamente:
```bash
flutter build ios --release --no-codesign
```
El archivo se generará en: `build/ios/iphoneos/Runner.app`

### Compilación para Dispositivo Físico (Con Firma)
Para instalar en un iPhone real o subir a la App Store:
1. Abre el proyecto en Xcode: `open ios/Runner.xcworkspace`
2. Ve a la pestaña **Signing & Capabilities**.
3. Selecciona tu **Team** (equipo de desarrollo).
4. Ejecuta:
   ```bash
   flutter build ios --release
   ```

---

## 4. Solución de Problemas Comunes

### Error: "Application not configured for iOS"
Si falta la carpeta `ios`, regenénala con:
```bash
flutter create --platforms=ios .
```

### Error: "Device is busy" o "Timed out waiting for destinations"
Esto ocurre cuando Xcode no logra comunicarse con el iPhone correctamente.
1. **Desbloquea el iPhone**: Asegúrate de que la pantalla esté encendida y desbloqueada.
2. **Reconecta el cable**: Desconecta y vuelve a conectar el cable USB.
3. **Xcode "Preparing device"**: Abre Xcode (`open ios/Runner.xcworkspace`), mira la barra de progreso superior. A veces Xcode tarda unos minutos en "preparar" el dispositivo tras una instalación o actualización.
4. **Desactivar conexión inalámbrica**: 
   - En Xcode, ve a **Window > Devices and Simulators**.
   - Busca tu iPhone y **desmarca** "Connect via network" (conectar vía red) para forzar que use solo el cable USB, que es más estable.

### Limpieza profunda (Si nada funciona)
```bash
flutter clean
flutter pub get
cd ios
rm -rf Pods Podfile.lock
pod install
cd ..
```

---

## 5. Instalación en iPhone Físico (Paso a Paso)

Para probar la app en tu propio iPhone, sigue estos pasos:

### 1. Conexión Física
- Conecta tu iPhone al Mac mediante un cable USB.
- Si aparece un mensaje en el iPhone preguntando **"¿Confiar en este ordenador?"**, pulsa **Confiar** e introduce tu código.

### 2. Configurar Firma (Solo la primera vez)
Apple no permite instalar apps en iPhones sin una firma digital.
1. Abre el proyecto en Xcode: `open ios/Runner.xcworkspace`
2. En el panel de la izquierda, haz clic en el icono azul de **Runner** (el primero de la lista).
3. Ve a la pestaña **Signing & Capabilities**.
4. En **Team**, selecciona tu cuenta (puede ser tu Apple ID personal).
5. Si ves un error de "Bundle Identifier", puedes cambiarlo a algo único (ej: `com.miguel.asistencia.scanner`).

### 3. Ejecutar la App
Desde la terminal en la raíz del proyecto:
```bash
flutter run
```
*Si tienes varios dispositivos, Flutter te pedirá que elijas uno.*

### 4. Autorizar el Desarrollador en el iPhone
La primera vez que instales la app, no se abrirá de inmediato.
1. En tu iPhone, ve a **Ajustes > General > Gestión de dispositivos (o VPN y gestión de dispositivos)**.
2. Busca tu Apple ID en "App de desarrollador".
3. Pulsa en **"Confiar en [Tu Apple ID]"**.
4. ¡Listo! Ya puedes abrir la aplicación.
