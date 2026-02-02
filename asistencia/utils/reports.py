import csv
import base64
import functools
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
    # Lógica de estado: asistieron vs faltaron
    if estado == 'faltaron':
        # Solo mostrar inasistentes, limpiar asistencias
        asistencias = asistencias.none()
        
        # Estrategia para calcular faltas:
        # 1. Si hay Evento seleccionado -> Inasistentes a ESE evento.
        # 2. Si no hay Evento, pero hay Fecha (Inicio == Fin) -> Inasistentes a CUALQUIER evento de ese día.
        # 3. Si es un rango de fechas -> Es complejo (¿faltó a 1 o a todos?), por ahora pedimos Evento o Día único.
        
        target_events = None
        if evento:
            target_events = [evento]
        elif fecha_inicio and fecha_fin and fecha_inicio == fecha_fin:
            # Buscar eventos en ese día específico
            target_events = list(Evento.objects.filter(fecha=fecha_inicio))
        
        if target_events:
            # Buscamos usuarios que NO tengan asistencia en ninguno de los eventos target
            asistentes_ids = Asistencia.objects.filter(
                evento__in=target_events
            ).values_list('usuario_id', flat=True)
            
            # Excluimos a los que sí fueron
            usuarios_no_asistentes = Usuario.objects.filter(
                estado=Usuario.ESTADO_ACTIVO
            ).exclude(id__in=asistentes_ids)
            
            if dni:
                usuarios_no_asistentes = usuarios_no_asistentes.filter(dni__icontains=dni)
            
            usuarios_no_asistentes = usuarios_no_asistentes.distinct().order_by('apellido', 'nombre')
        else:
            # Si no hay evento ni día específico, devolver vacío para evitar reporte gigante "faltaron todos"
            usuarios_no_asistentes = Usuario.objects.none()

    elif estado == 'asistieron':
        # Solo mostrar asistentes
        usuarios_no_asistentes = None
    else:
        # "Todos" (Asistieron + Faltaron)
        # Solo calculamos faltaron si hay un contexto claro (Evento o Día Único)
        target_events = None
        if evento:
            target_events = [evento]
        elif fecha_inicio and fecha_fin and fecha_inicio == fecha_fin:
            target_events = list(Evento.objects.filter(fecha=fecha_inicio))
            
        if target_events:
            asistentes_ids = Asistencia.objects.filter(
                evento__in=target_events
            ).values_list('usuario_id', flat=True)
            
            usuarios_no_asistentes = Usuario.objects.filter(
                estado=Usuario.ESTADO_ACTIVO
            ).exclude(id__in=asistentes_ids)
            
            if dni:
                usuarios_no_asistentes = usuarios_no_asistentes.filter(dni__icontains=dni)
            
            usuarios_no_asistentes = usuarios_no_asistentes.distinct().order_by('apellido', 'nombre')
        else:
            usuarios_no_asistentes = None

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
    writer.writerow(['Socio', 'DNI', 'Fecha', 'Ingreso', 'Salida', 'Lugar', 'Evento', 'Estado', 'Confirmada'])
    
    for item in unified_list:
        if item.is_absent:
            # Registro de falta
            writer.writerow([
                f"{item.usuario.nombre} {item.usuario.apellido}",
                item.usuario.dni,
                item.fecha.strftime('%d/%m/%Y') if item.fecha else '',
                'AUSENTE',
                'AUSENTE',
                item.ubicacion.nombre if item.ubicacion else 'N/A',
                item.evento.nombre if item.evento else 'Sin evento',
                'FALTA',
                'N/A'
            ])
        else:
            # Registro de asistencia
            writer.writerow([
                f"{item.usuario.nombre} {item.usuario.apellido}",
                item.usuario.dni,
                item.fecha.strftime('%d/%m/%Y') if item.fecha else '',
                item.hora_ingreso.strftime('%H:%M') if item.hora_ingreso else 'No registrado',
                item.hora_salida.strftime('%H:%M') if item.hora_salida else 'No registrado',
                item.ubicacion.nombre if item.ubicacion else 'Sin ubicación',
                item.evento.nombre if item.evento else 'Sin evento',
                'ASISTIÓ',
                'Sí' if item.confirmada else 'No'
            ])
    return response

def generate_attendance_excel(unified_list, filename="asistencias.xlsx"):
    """
    Genera Excel desde la lista unificada de ReportItem.
    Distingue entre asistencias y faltas con colores diferentes.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte de Asistencias"

    # Estilos
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    absent_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    align_center = Alignment(horizontal="center", vertical="center")
    border_thin = Border(
        left=Side(style='thin'), 
        right=Side(style='thin'), 
        top=Side(style='thin'), 
        bottom=Side(style='thin')
    )

    # Encabezados
    headers = ['Socio', 'DNI', 'Fecha', 'Ingreso', 'Salida', 'Lugar', 'Evento', 'Estado', 'Confirmada']
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center
        cell.border = border_thin

    # Datos unificados
    for row_idx, item in enumerate(unified_list, 2):
        if item.is_absent:
            # Registro de falta
            data = [
                f"{item.usuario.nombre} {item.usuario.apellido}",
                item.usuario.dni,
                item.fecha.strftime('%d/%m/%Y') if item.fecha else '',
                'AUSENTE',
                'AUSENTE',
                item.ubicacion.nombre if item.ubicacion else 'N/A',
                item.evento.nombre if item.evento else 'Sin evento',
                'FALTA',
                'N/A'
            ]
            for col, value in enumerate(data, 1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.border = border_thin
                cell.fill = absent_fill  # Fondo rojo para faltas
                if col in [3, 4, 5, 8, 9]:
                    cell.alignment = align_center
                if col == 8:  # "FALTA"
                    cell.font = Font(bold=True, color="DC2626")
        else:
            # Registro de asistencia
            data = [
                f"{item.usuario.nombre} {item.usuario.apellido}",
                item.usuario.dni,
                item.fecha.strftime('%d/%m/%Y') if item.fecha else '',
                item.hora_ingreso.strftime('%H:%M') if item.hora_ingreso else '--',
                item.hora_salida.strftime('%H:%M') if item.hora_salida else '--',
                item.ubicacion.nombre if item.ubicacion else 'General',
                item.evento.nombre if item.evento else 'Sin evento',
                'ASISTIÓ',
                'SÍ' if item.confirmada else 'NO'
            ]
            for col, value in enumerate(data, 1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.border = border_thin
                if col in [3, 4, 5, 8, 9]:
                    cell.alignment = align_center

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

@functools.lru_cache(maxsize=1)
def get_logo_base64():
    """
    Obtiene el logo en formato base64 con caché en memoria.
    El caché se invalida automáticamente al reiniciar el servidor.
    """
    try:
        config = ConfiguracionSistema.objects.first()
        if config and config.logo:
            try:
                with open(config.logo.path, "rb") as image_file:
                    return base64.b64encode(image_file.read()).decode('utf-8')
            except (FileNotFoundError, IOError, OSError):
                # Logo configurado pero archivo no existe
                return None
    except Exception:
        # ConfiguracionSistema no existe o error de base de datos
        return None
    return None
