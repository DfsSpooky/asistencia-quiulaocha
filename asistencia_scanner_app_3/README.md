# Asistencia Scanner App

App Flutter para login de escaneadores y registro de asistencia mediante QR.

## Configuracion del backend

La URL del backend se define con `--dart-define`, asi que ya no hace falta editar el codigo para cambiar entre local y produccion.

Produccion:

```bash
flutter run --dart-define=API_BASE_URL=https://quiulacocha.theworkpc.com
```

Android Emulator:

```bash
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
```

Dispositivo fisico en red local:

```bash
flutter run --dart-define=API_BASE_URL=http://192.168.1.100:8000
```

## Comandos utiles

```bash
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
flutter build apk --release --dart-define=API_BASE_URL=https://quiulacocha.theworkpc.com
```
