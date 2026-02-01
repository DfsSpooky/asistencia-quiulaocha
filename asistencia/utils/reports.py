import csv
import base64
from datetime import datetime
import io
from django.http import HttpResponse, FileResponse
from django.template.loader import render_to_string
from weasyprint import HTML
from xhtml2pdf import pisa
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from ..models import Usuario, Asistencia, ConfiguracionSistema, Justificacion

def get_filtered_attendance_data(filters):
    """
    Centraliza la lógica de filtrado de asistencias e inasistencias.
    """
    asistencias = Asistencia.objects.select_related('usuario', 'ubicacion', 'evento').all()
    
    dni = filters.get('dni')
    fecha_inicio = filters.get('fecha_inicio')
    fecha_fin = filters.get('fecha_fin')
    evento = filters.get('evento')
    ubicacion = filters.get('ubicacion')
    confirmada = filters.get('confirmada')
    estado = filters.get('estado')
    ordenar_por = filters.get('ordenar_por')

    # Aplicar filtros base
    if dni:
        asistencias = asistencias.filter(usuario__dni__icontains=dni)
    if fecha_inicio:
        asistencias = asistencias.filter(fecha__gte=fecha_inicio)
    if fecha_fin:
        asistencias = asistencias.filter(fecha__lte=fecha_fin)
    if evento:
        asistencias = asistencias.filter(evento=evento)
    if ubicacion:
        asistencias = asistencias.filter(ubicacion=ubicacion)
    if confirmada:
        confirmada_bool = confirmada == 'true'
        asistencias = asistencias.filter(confirmada=confirmada_bool)
    
    # Ordenamiento
    if ordenar_por:
        asistencias = asistencias.order_by(ordenar_por)
    else:
        asistencias = asistencias.order_by('-fecha', '-hora_ingreso')

    usuarios_no_asistentes = None
    
    # Lógica de estado: asistieron vs faltaron
    if estado == 'faltaron':
        # Solo mostrar inasistentes, limpiar asistencias
        asistencias = asistencias.none()
        # Si hay evento, calcular inasistentes de ese evento
        if evento:
            asistentes_ids = Asistencia.objects.filter(evento=evento).values_list('usuario__id', flat=True)
            usuarios_no_asistentes = Usuario.objects.filter(estado=Usuario.ESTADO_ACTIVO).exclude(id__in=asistentes_ids)
            if dni:
                usuarios_no_asistentes = usuarios_no_asistentes.filter(dni__icontains=dni)
            usuarios_no_asistentes = usuarios_no_asistentes.order_by('apellido', 'nombre')
    elif estado == 'asistieron':
        # Solo mostrar asistentes, no calcular inasistentes
        usuarios_no_asistentes = None
    else:
        # Sin filtro de estado: mostrar ambos si hay evento
        if evento:
            asistentes_ids = Asistencia.objects.filter(evento=evento).values_list('usuario__id', flat=True)
            usuarios_no_asistentes = Usuario.objects.filter(estado=Usuario.ESTADO_ACTIVO).exclude(id__in=asistentes_ids)
            if dni:
                usuarios_no_asistentes = usuarios_no_asistentes.filter(dni__icontains=dni)
            usuarios_no_asistentes = usuarios_no_asistentes.order_by('apellido', 'nombre')

    # Adjuntar URLs de evidencia si es necesario
    event_ids = {a.evento_id for a in asistencias if a.evento_id}
    if event_ids:
        just_map = {(j.usuario_id, j.evento_id): j.evidencia.url if j.evidencia else None 
                    for j in Justificacion.objects.filter(evento_id__in=event_ids, estado='APROBADO')}
        for a in asistencias:
            if a.es_justificada:
                a.evidencia_url = just_map.get((a.usuario_id, a.evento_id))

    return {
        'asistencias': asistencias,
        'usuarios_no_asistentes': usuarios_no_asistentes,
    }

def generate_attendance_csv(asistencias, usuarios_no_asistentes=None):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="asistencias.csv"'
    writer = csv.writer(response)
    
    writer.writerow([
        'Usuario', 'DNI', 'Fecha', 'Hora de Ingreso', 
        'Hora de Salida', 'Ubicación', 'Evento', 'Confirmada'
    ])

    if usuarios_no_asistentes:
        writer.writerow([])
        writer.writerow(['Usuarios que no asistieron'])
        writer.writerow(['Nombre', 'Apellido', 'DNI'])
        for usuario in usuarios_no_asistentes:
            writer.writerow([usuario.nombre, usuario.apellido, usuario.dni])
    else:
        for a in asistencias:
            writer.writerow([
                f"{a.usuario.nombre} {a.usuario.apellido}",
                a.usuario.dni,
                a.fecha,
                a.hora_ingreso if a.hora_ingreso else 'No registrado',
                a.hora_salida if a.hora_salida else 'No registrado',
                a.ubicacion.nombre if a.ubicacion else 'Sin ubicación',
                a.evento.nombre if a.evento else 'Sin evento',
                'Sí' if a.confirmada else 'No'
            ])
    return response

def generate_attendance_excel(asistencias, usuarios_no_asistentes=None, filename="asistencias.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte de Asistencias"

    # Estilos
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    align_center = Alignment(horizontal="center", vertical="center")
    border_thin = Border(
        left=Side(style='thin'), 
        right=Side(style='thin'), 
        top=Side(style='thin'), 
        bottom=Side(style='thin')
    )

    # Encabezados
    headers = ['Socio', 'DNI', 'Fecha', 'Ingreso', 'Salida', 'Lugar', 'Evento', 'Confirmada']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center
        cell.border = border_thin

    # Datos de asistencias
    for row, a in enumerate(asistencias, 2):
        data = [
            f"{a.usuario.nombre} {a.usuario.apellido}",
            a.usuario.dni,
            a.fecha.strftime('%d/%m/%Y') if a.fecha else '',
            a.hora_ingreso.strftime('%H:%M') if a.hora_ingreso else '--',
            a.hora_salida.strftime('%H:%M') if a.hora_salida else '--',
            a.ubicacion.nombre if a.ubicacion else 'General',
            a.evento.nombre if a.evento else 'Sin evento',
            'SÍ' if a.confirmada else 'NO'
        ]
        for col, value in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.border = border_thin
            if col in [3, 4, 5, 8]: # Centrar columnas de fecha, horas y confirmada
                cell.alignment = align_center

    # Agregar inasistentes si existen
    if usuarios_no_asistentes:
        start_row = len(asistencias) + 4
        ws.cell(row=start_row, column=1, value="SOCIOS QUE NO ASISTIERON").font = Font(bold=True, color="FF0000")
        
        headers_no = ['Nombre', 'Apellido', 'DNI', 'Estado']
        for col, header in enumerate(headers_no, 1):
            cell = ws.cell(row=start_row + 1, column=col, value=header)
            cell.font = header_font
            cell.fill = PatternFill(start_color="EF4444", end_color="EF4444", fill_type="solid")
            cell.alignment = align_center
            cell.border = border_thin

        for row, u in enumerate(usuarios_no_asistentes, start_row + 2):
            data_no = [u.nombre, u.apellido, u.dni, u.get_estado_display()]
            for col, value in enumerate(data_no, 1):
                cell = ws.cell(row=row, column=col, value=value)
                cell.border = border_thin

    # Ajustar ancho de columnas
    for column_cells in ws.columns:
        length = max(len(str(cell.value)) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = length + 2

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

def generate_pdf_report(template_name, context, filename):
    html_string = render_to_string(template_name, context)
    
    try:
        print(f"Generando PDF con WeasyPrint: {filename}")
        pdf_file = HTML(string=html_string).write_pdf()
        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{filename}"'
        response['Content-Length'] = len(pdf_file)
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    except Exception as e:
        print(f"WeasyPrint falló, intentando fallback con xhtml2pdf: {str(e)}")
        try:
            result = io.BytesIO()
            pisa_status = pisa.CreatePDF(
                io.BytesIO(html_string.encode("utf-8")), 
                dest=result,
                encoding='utf-8'
            )
            if not pisa_status.err:
                pdf_data = result.getvalue()
                response = HttpResponse(pdf_data, content_type='application/pdf')
                response['Content-Disposition'] = f'inline; filename="{filename}"'
                response['Content-Length'] = len(pdf_data)
                response['X-Content-Type-Options'] = 'nosniff'
                return response
        except Exception as fallback_e:
            print(f"Fallback también falló: {str(fallback_e)}")
            
        return HttpResponse(f"Error al generar el reporte: {str(e)}", status=500)

def get_logo_base64():
    config = ConfiguracionSistema.objects.first()
    if config and config.logo:
        try:
            with open(config.logo.path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        except Exception:
            return None
    return None
