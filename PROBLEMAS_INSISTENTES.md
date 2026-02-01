# Problemas Insistentes y Soluciones (Template Rendering)

Este documento registra problemas recurrentes encontrados en los templates de Django (`lista_usuarios.html` y `historial_asistencias.html`) y sus soluciones definitivas.

## 1. TemplateSyntaxError: Could not parse the remainder

**Síntoma:**
Error de servidor 500 o traza de error mostrando:
`TemplateSyntaxError: Could not parse the remainder: '==val' from 'form.estado.value==val'`

**Causa:**
El motor de plantillas de Django requiere espacios explícitos alrededor de los operadores de comparación en las etiquetas `{% if %}`.
- ❌ Incorrecto: `{% if variable==valor %}`
- ✅ Correcto: `{% if variable == valor %}`

**Solución:**
Asegurarse siempre de dejar espacios alrededor de `==`, `!=`, etc.
Si el error persiste tras editar, usar `sed` para forzar el reemplazo sin alterar el resto del archivo:
```bash
sed -i '' 's/==val/ == val/g' "ruta/al/archivo.html"
sed -i '' 's/==target/ == target/g' "ruta/al/archivo.html"
```

## 2. Renderizado de Tags como Texto Plano

**Síntoma:**
Las variables se muestran literalmente en el navegador (ej. `{{ variable }}`) en lugar de su valor, o aparecen bloques de código rotos.

**Causa:**
Django no siempre procesa correctamente los tags de variable `{{ ... }}` si están divididos en múltiples líneas dentro de atributos HTML o scripts.
- ❌ Incorrecto:
  ```html
  value="{{ 
      form.campo.value 
  }}"
  ```
- ✅ Correcto: `value="{{ form.campo.value }}"`

**Solución:**
Consolidar los tags en una sola línea.
Comando para arreglar esto automáticamente (usando Perl para unir líneas entre llaves dobles):
```bash
perl -i -0777 -pe 's/\{\{\s*\n\s*/{{ /g' "ruta/al/archivo.html"
```

---
**Nota:** Al editar estos archivos, verificar ambas condiciones antes de guardar para evitar regresiones.
