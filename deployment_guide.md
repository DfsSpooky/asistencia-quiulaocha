# Guia Rapida Local - Sistema de Asistencia QR

## Backend Django

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Accesos:

- App: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/gestionsegura/`

## Flutter

```bash
cd asistencia_scanner_app_3
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
```

Build release:

```bash
flutter build apk --release --dart-define=API_BASE_URL=https://quiulacocha.theworkpc.com
```

## Notas

- Para Android Emulator usa `http://10.0.2.2:8000`.
- Para dispositivo fisico usa `http://TU_IP_LOCAL:8000`.
- Para produccion usa `https://quiulacocha.theworkpc.com`.
