# Guía de la Aplicación de Escaneo (Flutter)

Esta aplicación ha sido desarrollada para el personal de apoyo encargado de registrar las asistencias mediante el escaneo de códigos QR.

## Mejoras en el Backend (Django)
- **Autenticación por Token**: Se configuró `rest_framework.authtoken` para permitir el inicio de sesión desde dispositivos móviles.
- **Nuevos Endpoints API**:
    - `POST /api/login/`: Autentica al personal y devuelve un token de acceso.
    - `GET /api/eventos-activos/`: Lista los eventos que están activos hoy.
    - `GET /api/ubicaciones/`: Lista las ubicaciones disponibles para el escaneo.
    - `POST /api/registrar-asistencia/`: Registra el ingreso o salida del usuario escaneado.

## Características de la Aplicación Móvil
- **Diseño Premium y Branding**: Amarillo vibrante (`#EAB308`) y Azul Pizarra (`#0F172A`) con logo oficial.
- **Funciones Avanzadas**:
    - **Modo Offline**: Los escaneos se guardan si no hay internet y se sincronizan al recuperar la señal.
    - **Control de Linterna**: Botón en el escáner para ambientes oscuros.
    - **Feedback Háptico/Sonoro**: Vibración y sonido al detectar un QR.
    - **Verificación Visual**: Muestra el nombre y **foto de perfil** del socio al escanear.
    - **Dashboard de Sesión**: Contador de escaneos y lista de actividad reciente en el inicio.
- **Inicio de Sesión**: Acceso seguro con las credenciales de Django.
- **Configuración de Escaneo**: Permite elegir el evento, la ubicación y si se registra una **Entrada** o **Salida**.
- **Escáner QR**: Integración fluida con la cámara para una lectura rápida.
- **Retroalimentación en Tiempo Real**: Mensajes claros de éxito o error tras cada escaneo.

## Calidad del Código y Buenas Prácticas
- **Análisis Limpio**: Se corrigieron todos los lints (keys, async gaps, APIs obsoletas).
- **Robustez**: Validaciones de estado para evitar errores en llamadas asíncronas.

## Cómo Ejecutar
1.  **Backend**:
    ```bash
    ./venv/bin/python manage.py runserver
    ```
2.  **Aplicación Flutter**:
    ```bash
    cd asistencia_scanner_app
    flutter run
    ```

> [!IMPORTANT]
> Asegúrate de que la dirección `baseUrl` en `lib/api_service.dart` apunte correctamente a tu servidor local o URL de ngrok.
