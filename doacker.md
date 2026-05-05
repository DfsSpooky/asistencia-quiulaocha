### ⚡ Hot Reload (Recarga en Vivo)
¡No necesitas hacer `build` cada vez que cambias tu código! 

Gracias a los **Volúmenes** y el file `docker-compose.override.yml`:
1. Si editas un `.py`, `.html` o `.css`, el servidor se reinicia **solo** dentro de Docker.
2. Los cambios se ven al instante en el navegador.

---

### 🚀 Guía de Comandos Rápidos

**1. Iniciar normalmente (MUY RÁPIDO):**
```bash
docker compose up -d
```

**2. Ver si hay errores en tiempo real (Logs):**
```bash
docker compose logs -f web
```

**3. ¿Cuándo hacer `--build`?**
Solo si instalas una librería nueva en `requirements.txt` o cambiaste el `Dockerfile`.
```bash
docker compose up -d --build
```

**4. Ejecutar cambios en la base de datos:**
```bash
docker compose exec web python manage.py migrate
```

---

### 💡 Tips Pro para Docker Desktop:
- **Detener servicios**: `docker compose stop`
- **Limpiar todo**: `docker compose down`
- **Revisar hora de Perú**: `docker compose exec web date`
- **Entrar a la consola de Django**: `docker compose exec web python manage.py shell`

¡Ya no tienes que esperar 10 minutos! Con el nuevo `.dockerignore`, el build será instantáneo.