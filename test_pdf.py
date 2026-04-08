import os
from datetime import datetime


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qr_asistencia.settings")

    import django

    django.setup()

    from django.template.loader import render_to_string

    from asistencia.models import Usuario
    from asistencia.utils.reports import get_image_base64

    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise SystemExit("WeasyPrint no esta instalado en este entorno.") from exc

    usuarios = Usuario.objects.all().order_by("apellido", "nombre")
    print(f"Total usuarios a procesar: {usuarios.count()}")

    usuarios_data = []
    for index, usuario in enumerate(usuarios):
        usuarios_data.append(
            {
                "nombre": usuario.nombre,
                "apellido": usuario.apellido,
                "dni": usuario.dni,
                "estado": usuario.get_estado_display(),
                "id": usuario.id,
                "foto_base64": get_image_base64(usuario.foto_perfil),
                "qr_base64": get_image_base64(usuario.qr_code),
            }
        )
        if index % 100 == 0:
            print(f"Procesando... {index}")

    context = {
        "usuarios": usuarios_data,
        "current_date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_usuarios": len(usuarios_data),
        "sistema_config": None,
    }

    print("Renderizando HTML...")
    html_string = render_to_string("asistencia/reporte_todos_carnets.html", context)

    print("Generando PDF (WeasyPrint)...")
    HTML(string=html_string).write_pdf()
    print("PDF generado.")


if __name__ == "__main__":
    main()
