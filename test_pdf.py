import sys
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qr_asistencia.settings")
django.setup()

from asistencia.models import Usuario
from asistencia.utils.reports import get_image_base64, generate_pdf_report
from datetime import datetime

usuarios = Usuario.objects.all().order_by('apellido', 'nombre')
print(f"Total usuarios a procesar: {usuarios.count()}")

usuarios_data = []
for i, u in enumerate(usuarios):
    usuarios_data.append({
        'nombre': u.nombre,
        'apellido': u.apellido,
        'dni': u.dni,
        'estado': u.get_estado_display(),
        'id': u.id,
        'foto_base64': get_image_base64(u.foto_perfil),
        'qr_base64': get_image_base64(u.qr_code)
    })
    if i % 100 == 0:
        print(f"Procesando... {i}")

context = {
    'usuarios': usuarios_data,
    'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'total_usuarios': len(usuarios_data),
    'sistema_config': None
}

from django.template.loader import render_to_string
print("Renderizando HTML...")
html_string = render_to_string('asistencia/reporte_todos_carnets.html', context)

print("Generando PDF (Weasyprint)...")
from weasyprint import HTML
pdf_file = HTML(string=html_string).write_pdf()
print("PDF Generado!")
