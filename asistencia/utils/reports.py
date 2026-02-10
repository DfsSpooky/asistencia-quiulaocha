import csv
import base64
import os
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

from django.db.models import Q

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
        # Búsqueda general: Nombre, Apellido o DNI
        asistencias = asistencias.filter(
            Q(usuario__dni__icontains=dni) |
            Q(usuario__nombre__icontains=dni) |
            Q(usuario__apellido__icontains=dni)
        )

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
    asistencias_justificadas = asistencias.filter(es_justificada=True)
    asistencias_regulares = asistencias.filter(es_justificada=False)

    if estado == 'asistieron':
        asistencias = asistencias_regulares
        usuarios_no_asistentes = None
    elif estado in ['faltaron', 'faltas_justificadas', 'faltas_injustificadas']:
        if estado == 'faltaron':
            # Faltas = Faltas Justificadas (registros) + Faltas Totales (sin registro)
            asistencias = asistencias_justificadas
        elif estado == 'faltas_justificadas':
            # Solo faltas justificadas
            asistencias = asistencias_justificadas
        elif estado == 'faltas_injustificadas':
            # Faltas injustificadas = Sin registro y sin justificacion
            asistencias = asistencias.none()

        # Estrategia para calcular faltas (sin registro):
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
            # Buscamos usuarios que SI tengan asistencia (cualquier tipo, para excluirlos de la lista "sin registro")
            # OJO: Los que tienen asistencia justificada YA fueron incluidos arriba en 'asistencias', así que debemos excluirlos aquí
            # para no duplicarlos.
            asistentes_ids = Asistencia.objects.filter(
                evento__in=target_events
            ).values_list('usuario_id', flat=True)
            
            # Base de inasistentes (excluyendo a los que tienen CUALQUIER registro)
            base_inasistentes = Usuario.objects.filter(
                estado=Usuario.ESTADO_ACTIVO
            ).exclude(id__in=asistentes_ids)
            
            if dni:
                base_inasistentes = base_inasistentes.filter(
                    Q(dni__icontains=dni) |
                    Q(nombre__icontains=dni) |
                    Q(apellido__icontains=dni)
                )

            # Ahora filtramos por justificación si es necesario (para los que NO tienen registro)
            usuarios_finales = []
            
            target_ev = target_events[0] if len(target_events) == 1 else None 
            
            # Solo procesar "sin registro" si el estado lo permite
            allows_justified_missing = estado in ['faltaron', 'faltas_justificadas']
            allows_unjustified_missing = estado in ['faltaron', 'faltas_injustificadas']

            for u in base_inasistentes:
                is_justified = False
                just_obs = ""
                
                if target_ev:
                    just = Justificacion.objects.filter(usuario=u, evento=target_ev, estado='APROBADO').first()
                    is_justified = just is not None
                    just_obs = just.motivo if just else ""
                
                include_user = False
                if is_justified and allows_justified_missing:
                    include_user = True
                elif not is_justified and allows_unjustified_missing:
                    include_user = True
                
                if include_user:
                    # Adjuntamos atributos temporales para el reporte
                    u.is_absent = True
                    u.es_justificada = is_justified
                    u.justificacion_obs = just_obs
                    u.evento = target_ev
                    u.fecha = target_ev.fecha if target_ev else None
                    usuarios_finales.append(u)
            
            usuarios_no_asistentes = usuarios_finales
        else:
            # Sin contexto suficiente para calcular faltas sin registro
            usuarios_no_asistentes = []

    else:
        # Estado "Todos" o vacío
        pass # asistencias se mantiene completo (regulares + justificadas)
        # Calcular faltantes sin registro (si hay contexto)
        target_events = None
        if evento:
            target_events = [evento]
        elif fecha_inicio and fecha_fin and fecha_inicio == fecha_fin:
            target_events = list(Evento.objects.filter(fecha=fecha_inicio))
            
        if target_events:
            asistentes_ids = Asistencia.objects.filter(
                evento__in=target_events
            ).values_list('usuario_id', flat=True)
            
            usuarios_no_asistentes_base = Usuario.objects.filter(
                estado=Usuario.ESTADO_ACTIVO
            ).exclude(id__in=asistentes_ids)
            
            if dni:
                usuarios_no_asistentes_base = usuarios_no_asistentes_base.filter(
                    Q(dni__icontains=dni) |
                    Q(nombre__icontains=dni) |
                    Q(apellido__icontains=dni)
                )
            
            # Convertir QuerySet a lista de objetos enriquecidos
            lista_inasistentes = []
            target_ev = target_events[0] if len(target_events) == 1 else None
            
            for u in usuarios_no_asistentes_base:
                just = None
                if target_ev:
                    just = Justificacion.objects.filter(usuario=u, evento=target_ev, estado='APROBADO').first()
                
                u.is_absent = True
                u.es_justificada = just is not None
                u.justificacion_obs = just.motivo if just else ""
                u.evento = target_ev
                u.fecha = target_ev.fecha if target_ev else None
                lista_inasistentes.append(u)
            
            usuarios_no_asistentes = lista_inasistentes

        elif dni:
            usuarios_no_asistentes = None
        else:
            usuarios_no_asistentes = None

    # Unificar en una lista coherente para reportes
    unified_report = []
    
    # Agregar asistentes (y faltas justificadas con registro)
    for a in asistencias:
        # Si es justificada, la tratamos como ausencia para efectos visuales (rojo/púrpura)
        a.is_absent = a.es_justificada
        unified_report.append(a)
    
    # Agregar inasistentes (sin registro)
    if usuarios_no_asistentes:
        for u in usuarios_no_asistentes:
             unified_report.append(u)

    return {
        'asistencias': asistencias,
        'usuarios_no_asistentes': usuarios_no_asistentes,
        'unified_report': unified_report,
    }

def generate_attendance_csv(unified_list):
    """
    Genera CSV desde la lista unificada de ReportItem.
    Distingue entre asistencias y faltas usando is_absent.
    """
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="reporte_asistencias.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Socio', 'DNI', 'Fecha', 'Ingreso', 'Salida', 'Lugar', 'Evento', 'Estado', 'Confirmada'])
    
    for item in unified_list:
        if item.is_absent:
            estado_texto = 'JUSTIFICADA' if item.es_justificada else 'FALTA'
            writer.writerow([
                f"{item.usuario.nombre} {item.usuario.apellido}",
                item.usuario.dni,
                item.fecha.strftime('%d/%m/%Y') if item.fecha else '',
                'JUSTIFICADO' if item.es_justificada else 'AUSENTE',
                'JUSTIFICADO' if item.es_justificada else 'AUSENTE',
                item.ubicacion.nombre if item.ubicacion else 'N/A',
                item.evento.nombre if item.evento else 'Sin evento',
                estado_texto,
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

def get_logo_base64():
    """
    Obtiene el logo en formato base64.
    """
    try:
        config = ConfiguracionSistema.objects.first()
        if config and config.logo:
            return get_image_base64(config.logo)
    except Exception:
        return None
    return None

def get_image_base64(image_field):
    """
    Convierte un ImageField a base64 para embeber en PDF.
    """
    if not image_field:
        return None
    try:
        # Intentar obtener la ruta absoluta
        path = image_field.path
        if os.path.exists(path):
            with open(path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
    except (FileNotFoundError, IOError, OSError, ValueError, AttributeError):
        pass
    return None

def generate_global_attendance_excel(report_data, system_config):
    """
    Genera un Excel Premium con múltiples hojas: Resumen y Detalle.
    """
    from io import BytesIO
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    
    # --- HOJA 1: RESUMEN EJECUTIVO ---
    ws_summary = wb.active
    ws_summary.title = "Resumen Ejecutivo"
    
    # Estilos Premium
    indigo_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    slate_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    white_font = Font(bold=True, color="FFFFFF")
    title_font = Font(bold=True, size=16, color="1E1B4B")
    subtitle_font = Font(bold=True, size=12, color="4F46E5")
    border_thin = Border(
        left=Side(style='thin', color="E2E8F0"), 
        right=Side(style='thin', color="E2E8F0"), 
        top=Side(style='thin', color="E2E8F0"), 
        bottom=Side(style='thin', color="E2E8F0")
    )

    # Título Principal
    ws_summary.merge_cells('B2:F2')
    cell_title = ws_summary['B2']
    cell_title.value = (system_config.nombre_institucion if system_config else "SISTEMA DE ASISTENCIA").upper()
    cell_title.font = title_font
    cell_title.alignment = Alignment(horizontal="center")

    ws_summary.merge_cells('B3:F3')
    cell_subtitle = ws_summary['B3']
    cell_subtitle.value = f"REPORTE GLOBAL CONSOLIDADO - {datetime.now().year}"
    cell_subtitle.font = subtitle_font
    cell_subtitle.alignment = Alignment(horizontal="center")

    # Tabla de Totales Generales
    ws_summary['B5'] = "MÉTRICA"
    ws_summary['C5'] = "VALOR"
    for cell in [ws_summary['B5'], ws_summary['C5']]:
        cell.fill = slate_fill
        cell.font = white_font
        cell.alignment = Alignment(horizontal="center")
        cell.border = border_thin

    total_eventos = len(report_data)
    total_padrón_acumulado = sum(ev['stats']['total_usuarios'] for ev in report_data)
    total_presentes_acumulado = sum(ev['stats']['confirmadas'] for ev in report_data)
    promedio_asistencia = (total_presentes_acumulado / total_padrón_acumulado * 100) if total_padrón_acumulado > 0 else 0

    metrics = [
        ("Total Eventos", total_eventos),
        ("Padrón Total (Acumulado)", total_padrón_acumulado),
        ("Asistencias Totales", total_presentes_acumulado),
        ("Promedio de Asistencia", f"{promedio_asistencia:.1f}%")
    ]

    for i, (m, v) in enumerate(metrics, start=6):
        ws_summary.cell(row=i, column=2, value=m).border = border_thin
        ws_summary.cell(row=i, column=3, value=v).border = border_thin
        ws_summary.cell(row=i, column=3).alignment = Alignment(horizontal="center")

    # Tabla de Detalle por Evento en el Resumen
    start_row_events = 12
    ws_summary.cell(row=start_row_events, column=2, value="EVENTO").fill = indigo_fill
    ws_summary.cell(row=start_row_events, column=2).font = white_font
    ws_summary.cell(row=start_row_events, column=3, value="FECHA").fill = indigo_fill
    ws_summary.cell(row=start_row_events, column=3).font = white_font
    ws_summary.cell(row=start_row_events, column=4, value="PADRÓN").fill = indigo_fill
    ws_summary.cell(row=start_row_events, column=4).font = white_font
    ws_summary.cell(row=start_row_events, column=5, value="ASISTIERON").fill = indigo_fill
    ws_summary.cell(row=start_row_events, column=5).font = white_font
    ws_summary.cell(row=start_row_events, column=6, value="% ASISTENCIA").fill = indigo_fill
    ws_summary.cell(row=start_row_events, column=6).font = white_font

    for i, ev_data in enumerate(report_data, 1):
        row = start_row_events + i
        ws_summary.cell(row=row, column=2, value=ev_data['evento'].nombre).border = border_thin
        ws_summary.cell(row=row, column=3, value=ev_data['evento'].fecha.strftime('%d/%m/%Y')).border = border_thin
        ws_summary.cell(row=row, column=4, value=ev_data['stats']['total_usuarios']).border = border_thin
        ws_summary.cell(row=row, column=5, value=ev_data['stats']['confirmadas']).border = border_thin
        ws_summary.cell(row=row, column=6, value=f"{ev_data['stats']['porcentaje']:.1f}%").border = border_thin
        
        for col in range(3, 7):
            ws_summary.cell(row=row, column=col).alignment = Alignment(horizontal="center")

    # Ajustar anchos
    ws_summary.column_dimensions['B'].width = 35
    ws_summary.column_dimensions['C'].width = 15
    ws_summary.column_dimensions['D'].width = 15
    ws_summary.column_dimensions['E'].width = 15
    ws_summary.column_dimensions['F'].width = 15

    # --- HOJA 2: DETALLE COMPLETO ---
    ws_detail = wb.create_sheet("Detalle de Asistencias")
    
    headers = ['EVENTO', 'SOCIO', 'DNI', 'ESTADO', 'INGRESO', 'SALIDA', 'OBSERVACIÓN']
    for col, head in enumerate(headers, 1):
        cell = ws_detail.cell(row=1, column=col, value=head)
        cell.fill = slate_fill
        cell.font = white_font
        cell.alignment = Alignment(horizontal="center")
    
    curr_row = 2
    for ev_data in report_data:
        for rec in ev_data['records']:
            ws_detail.cell(row=curr_row, column=1, value=ev_data['evento'].nombre)
            ws_detail.cell(row=curr_row, column=2, value=f"{rec['usuario'].nombre} {rec['usuario'].apellido}")
            ws_detail.cell(row=curr_row, column=3, value=rec['usuario'].dni)
            
            # Lógica de Estado
            status_text = "ASISTIÓ"
            if rec['is_absent']:
                status_text = "JUSTIFICADA" if rec['es_justificada'] else "FALTA"
            
            cell_status = ws_detail.cell(row=curr_row, column=4, value=status_text)
            if status_text == "FALTA":
                cell_status.font = Font(color="DC2626", bold=True)
            elif status_text == "JUSTIFICADA":
                cell_status.font = Font(color="4F46E5", bold=True)
            else:
                cell_status.font = Font(color="059669", bold=True)
                
            ws_detail.cell(row=curr_row, column=5, value=rec.get('hora_ingreso').strftime('%H:%M:%S') if rec.get('hora_ingreso') else "--")
            ws_detail.cell(row=curr_row, column=6, value=rec.get('hora_salida').strftime('%H:%M:%S') if rec.get('hora_salida') else "--")
            ws_detail.cell(row=curr_row, column=7, value=rec.get('justificacion_obs', ""))
            
            # Formato
            for c in range(1, 8):
                ws_detail.cell(row=curr_row, column=c).border = border_thin
            
            curr_row += 1

    # Ajustar anchos detalle
    ws_detail.column_dimensions['A'].width = 25
    ws_detail.column_dimensions['B'].width = 35
    ws_detail.column_dimensions['C'].width = 12
    ws_detail.column_dimensions['D'].width = 15
    ws_detail.column_dimensions['E'].width = 12
    ws_detail.column_dimensions['F'].width = 12
    ws_detail.column_dimensions['G'].width = 30
    
    ws_detail.freeze_panes = "A2" # Inmovilizar cabecera

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="reporte_global_{datetime.now().year}.xlsx"'
    return response
