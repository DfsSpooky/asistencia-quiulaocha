import csv
import base64
import os
import functools
from datetime import datetime
import io
from io import BytesIO
from django.http import HttpResponse, FileResponse
from django.template.loader import render_to_string
try:
    from weasyprint import HTML
except ImportError:
    HTML = None

try:
    from xhtml2pdf import pisa
except ImportError:
    pisa = None

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from ..models import Usuario, Asistencia, ConfiguracionSistema, Justificacion, Evento
from .attendance_rules import classify_attendance_item

from django.db.models import Q

def get_filtered_attendance_data(filters):
    """
    Centraliza la lógica de filtrado de asistencias e inasistencias.
    """
    asistencias_base = Asistencia.objects.select_related('usuario', 'ubicacion', 'evento').all()

    config = ConfiguracionSistema.objects.first()
    tardanza_activa = bool(config and config.tardanza_activa)
    
    dni = filters.get('dni')
    fecha_inicio = filters.get('fecha_inicio')
    fecha_fin = filters.get('fecha_fin')
    evento = filters.get('evento')
    tipo_evento = filters.get('tipo_evento')
    categoria_evento = filters.get('categoria_evento')
    ubicacion = filters.get('ubicacion')
    confirmada = filters.get('confirmada')
    estado = filters.get('estado')
    ordenar_por = filters.get('ordenar_por')

    # Estrategia para determinar eventos objetivo (target_events):
    # 1. Si hay Evento seleccionado -> ese evento.
    # 2. Si hay Rango de Fechas -> eventos en ese rango.
    # 3. Si solo hay fecha_inicio -> evento exacto en esa fecha o posteriores.
    # 4. Si solo hay fecha_fin -> evento exacto en esa fecha o anteriores.
    target_events = None
    if evento:
        target_events = [evento]
    elif fecha_inicio and fecha_fin:
        target_events = list(Evento.objects.filter(fecha__range=[fecha_inicio, fecha_fin]).order_by('fecha'))
    elif fecha_inicio:
        exact_events = list(Evento.objects.filter(fecha=fecha_inicio).order_by('fecha'))
        if exact_events:
            target_events = exact_events
        else:
            target_events = list(Evento.objects.filter(fecha__gte=fecha_inicio).order_by('fecha'))
    elif fecha_fin:
        exact_events = list(Evento.objects.filter(fecha=fecha_fin).order_by('fecha'))
        if exact_events:
            target_events = exact_events
        else:
            target_events = list(Evento.objects.filter(fecha__lte=fecha_fin).order_by('fecha'))
    else:
        # Si no se especifica evento ni rango de fechas (ej. descarga general sin filtros),
        # se toman todos los eventos registrados para que en el consolidado y en la pestaña
        # "Padrón General", cada reunión/asamblea cuente con la totalidad de sus 584 comuneros.
        target_events = list(Evento.objects.all().order_by('fecha'))

    # Filtrar target_events por tipo de reunión o categoría (Asamblea vs Faena)
    if target_events:
        if tipo_evento:
            t_id = tipo_evento.id if hasattr(tipo_evento, 'id') else tipo_evento
            target_events = [
                ev for ev in target_events
                if getattr(ev, 'tipo_evento_id', None) == t_id or ev.tipo_nombre == str(tipo_evento)
            ]
        if categoria_evento:
            cat_upper = str(categoria_evento).upper()
            if cat_upper == 'FAENA':
                target_events = [ev for ev in target_events if ev.es_faena]
            elif cat_upper == 'ASAMBLEA':
                target_events = [ev for ev in target_events if ev.es_asamblea]
            elif cat_upper == 'OTRO':
                target_events = [ev for ev in target_events if not ev.es_faena and not ev.es_asamblea]

    # Aplicar filtros base
    if dni:
        # Búsqueda general: Nombre, Apellido o DNI
        asistencias_base = asistencias_base.filter(
            Q(usuario__dni__icontains=dni) |
            Q(usuario__nombre__icontains=dni) |
            Q(usuario__apellido__icontains=dni)
        )

    if evento:
        asistencias_base = asistencias_base.filter(evento=evento)
    elif target_events:
        asistencias_base = asistencias_base.filter(evento__in=target_events)
    else:
        if fecha_inicio:
            asistencias_base = asistencias_base.filter(fecha__gte=fecha_inicio)
        if fecha_fin:
            asistencias_base = asistencias_base.filter(fecha__lte=fecha_fin)

    if ubicacion:
        asistencias_base = asistencias_base.filter(ubicacion=ubicacion)

    asistencias_confirmadas = asistencias_base
    if confirmada:
        confirmada_bool = confirmada == 'true'
        asistencias_confirmadas = asistencias_base.filter(confirmada=confirmada_bool)

    usuarios_no_asistentes = None
    
    # Lógica de estado: asistieron vs faltaron
    asistencias_justificadas = asistencias_base.filter(es_justificada=True)
    asistencias_regulares = asistencias_base.filter(es_justificada=False)
    tardy_asistencias = None
    if tardanza_activa:
        # Los exonerados no deben penalizarse por tardanza.
        tardy_asistencias = asistencias_regulares.filter(
            puntualidad=Asistencia.PUNTUALIDAD_TARDE
        ).exclude(usuario__estado=Usuario.ESTADO_EXONERADO)

    if estado == 'asistieron':
        asistencias = asistencias_confirmadas.filter(es_justificada=False)
        if tardanza_activa:
            asistencias = asistencias.exclude(
                puntualidad=Asistencia.PUNTUALIDAD_TARDE,
                usuario__estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_PASIVO],
            )
        usuarios_no_asistentes = None
    elif estado == 'pendientes':
        # Pendientes: Tienen ingreso pero NO tiene salida
        # Pendientes: Tienen ingreso pero NO tiene salida (y NO son exonerados)
        asistencias = asistencias_confirmadas.filter(hora_salida__isnull=True).exclude(usuario__estado='EXONERADO')
        if tardanza_activa:
            asistencias = asistencias.exclude(
                puntualidad=Asistencia.PUNTUALIDAD_TARDE,
                usuario__estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_PASIVO],
            )
        usuarios_no_asistentes = None
    elif estado in ['faltaron', 'faltas_justificadas', 'faltas_injustificadas']:
        if estado == 'faltaron':
            # Faltas = Faltas Justificadas (registros) + Faltas Totales (sin registro)
            asistencias = asistencias_justificadas
            if tardanza_activa and tardy_asistencias is not None:
                asistencias = (asistencias | tardy_asistencias).distinct()
        elif estado == 'faltas_justificadas':
            # Solo faltas justificadas
            asistencias = asistencias_justificadas
        elif estado == 'faltas_injustificadas':
            # Faltas injustificadas = Sin registro y sin justificacion
            asistencias = asistencias_base.none()
            if tardanza_activa and tardy_asistencias is not None:
                asistencias = tardy_asistencias

        # Calcular faltas (sin registro) para target_events
        usuarios_finales = []
        
        if target_events:
            allows_justified_missing = estado in ['faltaron', 'faltas_justificadas']
            allows_unjustified_missing = estado in ['faltaron', 'faltas_injustificadas']
            
            # Optimizacion: Traer todas las asistencias y justificaciones en el rango de una vez
            all_attendances = set(Asistencia.objects.filter(evento__in=target_events).values_list('usuario_id', 'evento_id'))
            all_justifications = {}
            just_qs = Justificacion.objects.filter(evento__in=target_events, estado='APROBADO').select_related('evento', 'usuario')
            for j in just_qs:
                all_justifications[(j.usuario_id, j.evento_id)] = j

            # Base de usuarios activos
            base_users_qs = Usuario.objects.filter(
                estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]
            )
            if dni:
                base_users_qs = base_users_qs.filter(
                    Q(dni__icontains=dni) |
                    Q(nombre__icontains=dni) |
                    Q(apellido__icontains=dni)
                )
            base_users = list(base_users_qs)

            # Iterar por cada evento para encontrar quién faltó a QUÉ evento
            for ev in target_events:
                for u in base_users:
                    # Si tiene asistencia registrada para este evento, NO es falta (ya está en 'asistencias')
                    # Nota: Las faltas justificadas que generaron registro en Asistencia (es_justificada=True)
                    # ya están en la lista principal 'asistencias'. Aquí buscamos solo los SIN REGISTRO.
                    if (u.id, ev.id) in all_attendances:
                        continue

                    # Verificar si tiene justificación (pero sin registro de asistencia, caso raro pero posible)
                    just = all_justifications.get((u.id, ev.id))
                    is_justified = just is not None

                    include_user = False
                    if is_justified and allows_justified_missing:
                        include_user = True
                    elif not is_justified and allows_unjustified_missing:
                        include_user = True

                    if include_user:
                        # Clonar usuario para no modificar la referencia compartida en el loop
                        # o usar un objeto proxy simple
                        import copy
                        u_proxy = copy.copy(u) # Shallow copy es suficiente para adjuntar atributos
                        u_proxy.is_absent = True
                        u_proxy.es_justificada = is_justified
                        u_proxy.justificacion_obs = just.motivo if just else ""
                        u_proxy.evento = ev
                        u_proxy.fecha = ev.fecha
                        usuarios_finales.append(u_proxy)
            
            usuarios_no_asistentes = usuarios_finales
        else:
            usuarios_no_asistentes = []

    else:
        # Estado "Todos" o vacío
        asistencias = asistencias_confirmadas
        # Calcular faltantes sin registro (si hay contexto de eventos)
        if target_events:
            base_users_qs = Usuario.objects.filter(
                estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]
            )
            if dni:
                base_users_qs = base_users_qs.filter(
                    Q(dni__icontains=dni) |
                    Q(nombre__icontains=dni) |
                    Q(apellido__icontains=dni)
                )

            base_users = list(base_users_qs)
            all_attendances = set(
                Asistencia.objects.filter(evento__in=target_events).values_list('usuario_id', 'evento_id')
            )
            all_justifications = {}
            just_qs = Justificacion.objects.filter(
                evento__in=target_events,
                estado='APROBADO'
            ).select_related('evento', 'usuario')
            for j in just_qs:
                all_justifications[(j.usuario_id, j.evento_id)] = j
            
            lista_inasistentes = []
            
            for ev in target_events:
                for u in base_users:
                    if (u.id, ev.id) in all_attendances:
                        continue

                    just = all_justifications.get((u.id, ev.id))
                    import copy
                    u_proxy = copy.copy(u)
                    u_proxy.is_absent = True
                    u_proxy.es_justificada = just is not None
                    u_proxy.justificacion_obs = just.motivo if just else ""
                    u_proxy.evento = ev
                    u_proxy.fecha = ev.fecha
                    lista_inasistentes.append(u_proxy)
            
            usuarios_no_asistentes = lista_inasistentes

        elif dni:
            usuarios_no_asistentes = None
        else:
            usuarios_no_asistentes = None

    # Ordenamiento final una vez resuelto el subconjunto de registros.
    if ordenar_por:
        asistencias = asistencias.order_by(ordenar_por)
    else:
        asistencias = asistencias.order_by('-fecha', '-hora_ingreso')

    # Mapa de motivos para registros marcados como justificados.
    # Permite mostrar el motivo en historial y reportes PDF.
    justificacion_map = {}
    justified_pairs = list(
        asistencias.filter(es_justificada=True).values_list('usuario_id', 'evento_id')
    )
    if justified_pairs:
        user_ids = {pair[0] for pair in justified_pairs if pair[0]}
        event_ids = {pair[1] for pair in justified_pairs if pair[1]}
        justificaciones_qs = Justificacion.objects.filter(
            estado='APROBADO',
            usuario_id__in=user_ids,
            evento_id__in=event_ids
        ).only('usuario_id', 'evento_id', 'motivo')
        justificacion_map = {
            (j.usuario_id, j.evento_id): j.motivo
            for j in justificaciones_qs
        }

    # Unificar en una lista coherente para reportes garantizando padrón sin duplicados
    unified_report = []
    seen_keys = set()
    
    # Agregar asistentes (y faltas justificadas con registro)
    for a in asistencias:
        flags = classify_attendance_item(a, tardanza_activa)
        a.is_late_absent = flags.is_late_absent
        # Si es justificada o tardía fuera de límite, la tratamos como ausencia.
        a.is_absent = flags.is_absent
        if a.es_justificada:
            a.justificacion_obs = justificacion_map.get((a.usuario_id, a.evento_id), "")
        
        ev_key = a.evento_id or getattr(a, 'fecha', None)
        key = (a.usuario_id, ev_key)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unified_report.append(a)
    
    # Agregar inasistentes (sin registro)
    if usuarios_no_asistentes:
        for u in usuarios_no_asistentes:
            ev_id = getattr(u, 'evento_id', None) or (u.evento.id if hasattr(u, 'evento') and u.evento else getattr(u, 'fecha', None))
            key = (u.id, ev_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unified_report.append(u)

    return {
        'asistencias': asistencias,
        'usuarios_no_asistentes': usuarios_no_asistentes,
        'unified_report': unified_report,
    }

def _extract_attendance_user(item):
    """
    Obtiene la instancia de Usuario ya sea de un registro Asistencia o de un proxy de Usuario.
    """
    if hasattr(item, 'usuario'):
        return item.usuario
    return item


def _normalize_attendance_record(item, tardanza_activa=True):
    """
    Normaliza cualquier ítem del reporte unificado (Asistencia o Usuario inasistente)
    a un diccionario consistente con todos los datos calculados.
    """
    usuario = _extract_attendance_user(item)
    is_asistencia_obj = hasattr(item, 'usuario')
    
    nombre_completo = f"{getattr(usuario, 'nombre', '')} {getattr(usuario, 'apellido', '')}".strip()
    dni = str(getattr(usuario, 'dni', '') or '').strip()
    condicion_socio = (
        usuario.get_estado_display() 
        if hasattr(usuario, 'get_estado_display') 
        else str(getattr(usuario, 'estado', 'Activo'))
    )
    
    fecha = getattr(item, 'fecha', None)
    if is_asistencia_obj and getattr(item, 'evento', None) and getattr(item.evento, 'fecha', None):
        fecha = item.evento.fecha
    fecha_str = fecha.strftime('%d/%m/%Y') if fecha else ''
    
    hora_ingreso = getattr(item, 'hora_ingreso', None)
    hora_salida = getattr(item, 'hora_salida', None)
    ubicacion = getattr(item, 'ubicacion', None)
    ubicacion_str = ubicacion.nombre if ubicacion else 'General'
    evento = getattr(item, 'evento', None)
    evento_str = evento.nombre if evento else 'General'
    tipo_evento_str = evento.tipo_nombre if evento else 'General'
    es_faena = bool(evento and evento.es_faena)
    es_asamblea = bool(not evento or evento.es_asamblea)
    categoria_tipo = evento.categoria_tipo if evento else 'ASAMBLEA'
    
    es_justificada = bool(getattr(item, 'es_justificada', False))
    justificacion_obs = getattr(item, 'justificacion_obs', '') or ''
    confirmada = bool(getattr(item, 'confirmada', False))
    puntualidad = getattr(item, 'puntualidad', None)
    
    if is_asistencia_obj:
        flags = classify_attendance_item(item, tardanza_activa)
        is_absent = flags.is_absent
        is_late_absent = flags.is_late_absent
        is_exonerado = flags.is_exonerado
        is_pending = flags.is_pending
    else:
        is_absent = True
        is_late_absent = False
        is_exonerado = getattr(usuario, 'estado', None) == Usuario.ESTADO_EXONERADO
        is_pending = False

    estado_usuario = getattr(usuario, 'estado', None)
    
    if is_absent:
        if es_justificada:
            estado_texto = "JUSTIFICADA"
            categoria = "JUSTIFICADA"
            ingreso_str = "JUSTIFICADO"
            salida_str = "JUSTIFICADO"
            puntualidad_texto = "No aplica"
            observacion = justificacion_obs if justificacion_obs else "Inasistencia justificada con documento"
        elif is_late_absent:
            estado_texto = "FALTA (TARDE)"
            categoria = "FALTA"
            ingreso_str = hora_ingreso.strftime('%H:%M') if hora_ingreso else "--"
            salida_str = hora_salida.strftime('%H:%M') if hora_salida else "--"
            puntualidad_texto = "Tardanza"
            observacion = "Tardanza fuera del límite de tolerancia"
            if justificacion_obs:
                observacion += f" | {justificacion_obs}"
        else:
            estado_texto = "FALTA"
            categoria = "FALTA"
            ingreso_str = "AUSENTE"
            salida_str = "AUSENTE"
            puntualidad_texto = "No aplica"
            observacion = justificacion_obs if justificacion_obs else "No asistió al evento convocado"
        confirmada_str = "Sí" if confirmada else "No"
    else:
        if is_exonerado or estado_usuario == Usuario.ESTADO_EXONERADO:
            estado_texto = "ASISTIÓ (EXONERADO)"
            categoria = "ASISTIO"
            ingreso_str = hora_ingreso.strftime('%H:%M') if hora_ingreso else "EXONERADO"
            salida_str = hora_salida.strftime('%H:%M') if hora_salida else "EXONERADO"
            puntualidad_texto = "Exonerado"
            confirmada_str = "Sí"
            observacion = "Socio exonerado (asistencia confirmada)"
        elif hora_salida:
            estado_texto = "ASISTIÓ"
            categoria = "ASISTIO"
            ingreso_str = hora_ingreso.strftime('%H:%M') if hora_ingreso else "--"
            salida_str = hora_salida.strftime('%H:%M') if hora_salida else "--"
            puntualidad_texto = (
                item.get_puntualidad_display()
                if (is_asistencia_obj and puntualidad)
                else "No aplica"
            )
            confirmada_str = "Sí" if confirmada else "No"
            if puntualidad == Asistencia.PUNTUALIDAD_PUNTUAL:
                observacion = "Ingreso puntual"
            elif puntualidad == Asistencia.PUNTUALIDAD_TARDE:
                observacion = "Ingreso con tardanza dentro de tolerancia"
            else:
                observacion = justificacion_obs or "Asistencia confirmada"
        else:
            estado_texto = "PENDIENTE"
            categoria = "PENDIENTE"
            ingreso_str = hora_ingreso.strftime('%H:%M') if hora_ingreso else "--"
            salida_str = "Sin salida"
            puntualidad_texto = (
                item.get_puntualidad_display()
                if (is_asistencia_obj and puntualidad)
                else "No aplica"
            )
            confirmada_str = "Sí" if confirmada else "No"
            observacion = "Registró ingreso pero no registró salida"

    return {
        'usuario': usuario,
        'nombre_completo': nombre_completo,
        'dni': dni,
        'condicion_socio': condicion_socio,
        'fecha': fecha,
        'fecha_str': fecha_str,
        'hora_ingreso_str': ingreso_str,
        'hora_salida_str': salida_str,
        'ubicacion_str': ubicacion_str,
        'evento_str': evento_str,
        'tipo_evento_str': tipo_evento_str,
        'es_faena': es_faena,
        'es_asamblea': es_asamblea,
        'categoria_tipo': categoria_tipo,
        'estado_texto': estado_texto,
        'categoria': categoria,
        'puntualidad_texto': puntualidad_texto,
        'confirmada_str': confirmada_str,
        'observacion': observacion,
        'is_absent': is_absent,
        'es_justificada': es_justificada,
        'is_late_absent': is_late_absent,
    }


def generate_attendance_csv(unified_list):
    """
    Genera CSV desde la lista unificada con codificación UTF-8 y BOM para Excel.
    """
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="reporte_asistencias.csv"'
    
    # BOM para que Excel en Windows abra correctamente acentos y la ñ
    response.write('\ufeff'.encode('utf-8'))
    
    writer = csv.writer(response)
    writer.writerow([
        'N°', 'Socio', 'DNI', 'Condición', 'Fecha', 'Ingreso', 
        'Salida', 'Lugar', 'Evento', 'Estado', 'Puntualidad', 'Confirmada', 'Observación'
    ])
    
    config = ConfiguracionSistema.objects.first()
    tardanza_activa = bool(config and config.tardanza_activa)
    
    for idx, item in enumerate(unified_list, 1):
        rec = _normalize_attendance_record(item, tardanza_activa)
        writer.writerow([
            idx,
            rec['nombre_completo'],
            rec['dni'],
            rec['condicion_socio'],
            rec['fecha_str'],
            rec['hora_ingreso_str'],
            rec['hora_salida_str'],
            rec['ubicacion_str'],
            rec['evento_str'],
            rec['estado_texto'],
            rec['puntualidad_texto'],
            rec['confirmada_str'],
            rec['observacion'],
        ])
    return response


def _build_excel_data_sheet(
    ws,
    sheet_title,
    tab_color,
    banner_title,
    banner_subtitle,
    headers,
    rows_data,
    header_fill,
    is_general=False,
):
    """
    Construye una pestaña de datos estilizada con Autofiltros, Freeze Panes,
    formato cebra, badges de estado y anchos de columna automáticos.
    """
    ws.title = sheet_title
    ws.sheet_properties.tabColor = tab_color
    ws.views.sheetView[0].showGridLines = True

    font_banner_title = Font(name="Calibri", size=13, bold=True, color="0F172A")
    font_banner_sub = Font(name="Calibri", size=9, bold=True, color="64748B")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10, color="0F172A")
    font_correlativo = Font(name="Calibri", size=10, color="64748B")

    fill_zebra_even = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    fill_zebra_odd = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    border_soft = Border(
        left=Side(style='thin', color="E2E8F0"),
        right=Side(style='thin', color="E2E8F0"),
        top=Side(style='thin', color="E2E8F0"),
        bottom=Side(style='thin', color="E2E8F0")
    )
    border_bottom_thick = Border(
        left=Side(style='thin', color="CBD5E1"),
        right=Side(style='thin', color="CBD5E1"),
        top=Side(style='thin', color="CBD5E1"),
        bottom=Side(style='medium', color="0F172A")
    )

    badge_styles = {
        'FALTA': {
            'fill': PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="991B1B"),
        },
        'ASISTIÓ': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="166534"),
        },
        'ASISTIO': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="166534"),
        },
        'EXONERADO': {
            'fill': PatternFill(start_color="F3E8FF", end_color="F3E8FF", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="6B21A8"),
        },
        'JUSTIFICADA': {
            'fill': PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="1E40AF"),
        },
        'PENDIENTE': {
            'fill': PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="92400E"),
        },
        'AL DÍA': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="166534"),
        },
        'AL DIA': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="166534"),
        },
        'OBSERVADO': {
            'fill': PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="92400E"),
        },
        'SANCIÓN': {
            'fill': PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="991B1B"),
        },
        'SANCION': {
            'fill': PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="991B1B"),
        },
    }

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Banners de cabecera
    ws.sheet_format.defaultRowHeight = 20
    ws.row_dimensions[1].height = 6
    ws.row_dimensions[2].height = 22
    ws.row_dimensions[3].height = 18
    ws.row_dimensions[4].height = 8
    ws.row_dimensions[5].height = 26

    ws['A2'] = banner_title
    ws['A2'].font = font_banner_title
    ws['A3'] = banner_subtitle
    ws['A3'].font = font_banner_sub

    # Encabezados en fila 5
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=5, column=col_idx, value=header)
        cell.font = font_header
        cell.fill = header_fill
        cell.alignment = align_header
        cell.border = border_bottom_thick

    # Datos en fila 6+
    curr_row = 6
    for idx, row in enumerate(rows_data, 1):
        row_fill = fill_zebra_even if (idx % 2 == 0) else fill_zebra_odd
        
        for col_idx, value in enumerate(row, 1):
            cell = ws.cell(row=curr_row, column=col_idx, value=value)
            cell.font = font_data
            cell.border = border_soft
            cell.fill = row_fill
            header_name = headers[col_idx - 1]
            
            val_str = str(value or '').strip()
            if header_name == 'N°':
                cell.alignment = align_center
                cell.font = font_correlativo
            elif header_name == 'DNI':
                cell.number_format = '@'
                cell.alignment = align_center
            elif header_name in ['FECHA', 'INGRESO', 'SALIDA', 'PUNTUALIDAD', 'CONFIRMADA']:
                cell.alignment = align_center
            elif header_name in ['ESTADO', 'ESTADO GENERAL', 'ESTADO COMUNAL']:
                cell.alignment = align_center
                upper_val = val_str.upper()
                matched_badge = None
                for k, b in badge_styles.items():
                    if k in upper_val:
                        matched_badge = b
                        break
                if matched_badge:
                    cell.fill = matched_badge['fill']
                    cell.font = matched_badge['font']
            else:
                cell.alignment = align_left

        curr_row += 1

    total_rows = len(rows_data)
    if total_rows == 0:
        ws.row_dimensions[6].height = 24
        cell_empty = ws.cell(row=6, column=1, value="No se encontraron registros en esta categoría.")
        cell_empty.font = Font(name="Calibri", size=10, italic=True, color="64748B")
        cell_empty.alignment = Alignment(horizontal="left", vertical="center")
        curr_row += 1

    ws.freeze_panes = "A6"

    last_col_letter = get_column_letter(len(headers))
    if total_rows > 0:
        ws.auto_filter.ref = f"A5:{last_col_letter}{curr_row - 1}"

    # Autoajuste de anchos de columna con padding seguro
    for col_idx in range(1, len(headers) + 1):
        col_letter = get_column_letter(col_idx)
        header_text = headers[col_idx - 1]
        max_len = len(str(header_text))
        
        sample_rows = rows_data[:150]
        for r in sample_rows:
            if col_idx - 1 < len(r):
                cell_len = len(str(r[col_idx - 1] or ''))
                if cell_len > max_len:
                    max_len = cell_len

        calc_width = max_len + 4
        
        if header_text == 'N°':
            ws.column_dimensions[col_letter].width = 6
        elif header_text == 'DNI':
            ws.column_dimensions[col_letter].width = 13
        elif 'APELLIDOS' in header_text or 'SOCIO' in header_text or 'COMUNERO' in header_text:
            ws.column_dimensions[col_letter].width = max(calc_width, 32)
        elif header_text in ['ESTADO', 'ESTADO GENERAL', 'ESTADO COMUNAL']:
            ws.column_dimensions[col_letter].width = max(calc_width, 22)
        elif 'OBSERVACIÓN' in header_text or 'MOTIVO' in header_text:
            ws.column_dimensions[col_letter].width = max(calc_width, 28)
        elif header_text in ['EVENTO', 'LUGAR', 'UBICACIÓN']:
            ws.column_dimensions[col_letter].width = max(calc_width, 20)
        elif 'REUNIONES' in header_text or 'FALTÓ' in header_text or 'FALTO' in header_text:
            ws.column_dimensions[col_letter].width = max(calc_width, 42)
        else:
            ws.column_dimensions[col_letter].width = max(calc_width, 14)


def _build_excel_summary_sheet(ws_resumen, config, evento_nombre, fecha_descarga, stats):
    """
    Construye la portada ejecutiva del libro Excel con tarjetas KPI y distribución.
    """
    ws_resumen.title = "Resumen Ejecutivo"
    ws_resumen.sheet_properties.tabColor = "1E293B"
    ws_resumen.views.sheetView[0].showGridLines = True

    font_title = Font(name="Calibri", size=15, bold=True, color="0F172A")
    font_subtitle = Font(name="Calibri", size=11, bold=True, color="334155")
    font_section = Font(name="Calibri", size=11, bold=True, color="1E293B")
    font_card_lbl = Font(name="Calibri", size=8, bold=True, color="64748B")
    font_header_table = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10, color="0F172A")
    font_bold = Font(name="Calibri", size=10, bold=True, color="0F172A")

    fill_header = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    fill_total = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    border_soft = Border(
        left=Side(style='thin', color="E2E8F0"),
        right=Side(style='thin', color="E2E8F0"),
        top=Side(style='thin', color="E2E8F0"),
        bottom=Side(style='thin', color="E2E8F0")
    )
    border_top_thick = Border(
        top=Side(style='medium', color="0F172A"),
        bottom=Side(style='double', color="0F172A"),
        left=Side(style='thin', color="E2E8F0"),
        right=Side(style='thin', color="E2E8F0"),
    )

    inst_nombre = (config.nombre_institucion if config else "COMUNIDAD CAMPESINA DE QUIULACOCHA").upper()
    ws_resumen.row_dimensions[2].height = 24
    ws_resumen.row_dimensions[3].height = 18
    ws_resumen.row_dimensions[4].height = 16

    ws_resumen['B2'] = inst_nombre
    ws_resumen['B2'].font = font_title
    ws_resumen['B3'] = f"REPORTE CONSOLIDADO DE ASISTENCIA - {evento_nombre}".upper()
    ws_resumen['B3'].font = font_subtitle
    ws_resumen['B4'] = f"Generado: {fecha_descarga} | Control Biométrico y QR"
    ws_resumen['B4'].font = Font(name="Calibri", size=9, italic=True, color="64748B")

    cards = [
        ('B', 'PADRÓN ESPERADO', stats['total_padron'], "F8FAFC", "0F172A"),
        ('C', 'ASISTIERON', stats['total_asistieron'], "DCFCE7", "166534"),
        ('D', 'JUSTIFICADAS', stats['total_justificadas'], "DBEAFE", "1E40AF"),
        ('E', 'FALTAS REALES', stats['total_faltas'], "FEE2E2", "991B1B"),
        ('F', 'PENDIENTES', stats['total_pendientes'], "FEF3C7", "92400E"),
        ('G', '% ASISTENCIA', f"{stats['porcentaje_asistencia']:.1f}%", "F3E8FF", "6B21A8"),
    ]

    ws_resumen.row_dimensions[6].height = 16
    ws_resumen.row_dimensions[7].height = 28

    for col_letter, label, val, bg_color, text_color in cards:
        cell_lbl = ws_resumen[f'{col_letter}6']
        cell_lbl.value = label
        cell_lbl.font = font_card_lbl
        cell_lbl.alignment = Alignment(horizontal="center", vertical="center")
        cell_lbl.fill = PatternFill(start_color=bg_color, end_color=bg_color, fill_type="solid")
        cell_lbl.border = Border(top=Side(style='thin', color="CBD5E1"), left=Side(style='thin', color="CBD5E1"), right=Side(style='thin', color="CBD5E1"))

        cell_val = ws_resumen[f'{col_letter}7']
        cell_val.value = val
        cell_val.font = Font(name="Calibri", size=16, bold=True, color=text_color)
        cell_val.alignment = Alignment(horizontal="center", vertical="center")
        cell_val.fill = PatternFill(start_color=bg_color, end_color=bg_color, fill_type="solid")
        cell_val.border = Border(bottom=Side(style='thin', color="CBD5E1"), left=Side(style='thin', color="CBD5E1"), right=Side(style='thin', color="CBD5E1"))

    ws_resumen['B10'] = "DISTRIBUCIÓN Y BALANCE DE ASISTENCIA"
    ws_resumen['B10'].font = font_section

    ws_resumen.row_dimensions[11].height = 22
    headers_resumen = ['CATEGORÍA / CONDICIÓN', 'CANTIDAD', '% PARTICIPACIÓN']
    for idx, h in enumerate(headers_resumen, 2):
        col_letter = get_column_letter(idx)
        cell = ws_resumen[f'{col_letter}11']
        cell.value = h
        cell.font = font_header_table
        cell.fill = fill_header
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border_soft

    padron_total = stats['total_padron']
    filas_resumen = [
        ("Asistieron (Efectivos y Exonerados)", stats['total_asistieron'], (stats['total_asistieron'] / padron_total * 100) if padron_total > 0 else 0),
        ("Inasistencias Justificadas", stats['total_justificadas'], (stats['total_justificadas'] / padron_total * 100) if padron_total > 0 else 0),
        ("Faltas Reales (Inasistencias)", stats['total_faltas'], (stats['total_faltas'] / padron_total * 100) if padron_total > 0 else 0),
        ("Pendientes (Sin salida registrada)", stats['total_pendientes'], (stats['total_pendientes'] / padron_total * 100) if padron_total > 0 else 0),
    ]

    r_idx = 12
    for cat, cant, pct in filas_resumen:
        ws_resumen.row_dimensions[r_idx].height = 20
        c1 = ws_resumen[f'B{r_idx}']
        c1.value = cat
        c1.font = font_data
        c1.border = border_soft

        c2 = ws_resumen[f'C{r_idx}']
        c2.value = cant
        c2.font = font_data
        c2.alignment = Alignment(horizontal="center", vertical="center")
        c2.border = border_soft

        c3 = ws_resumen[f'D{r_idx}']
        c3.value = f"{pct:.1f}%"
        c3.font = font_data
        c3.alignment = Alignment(horizontal="center", vertical="center")
        c3.border = border_soft
        r_idx += 1

    ws_resumen.row_dimensions[r_idx].height = 22
    t1 = ws_resumen[f'B{r_idx}']
    t1.value = "TOTAL PADRÓN REGISTRADO"
    t1.font = font_bold
    t1.fill = fill_total
    t1.border = border_top_thick

    t2 = ws_resumen[f'C{r_idx}']
    t2.value = padron_total
    t2.font = font_bold
    t2.fill = fill_total
    t2.alignment = Alignment(horizontal="center", vertical="center")
    t2.border = border_top_thick

    t3 = ws_resumen[f'D{r_idx}']
    t3.value = "100.0%"
    t3.font = font_bold
    t3.fill = fill_total
    t3.alignment = Alignment(horizontal="center", vertical="center")
    t3.border = border_top_thick

    guia_row = r_idx + 3
    ws_resumen[f'B{guia_row}'] = "GUÍA DE PESTAÑAS DE ESTE ARCHIVO EXCEL"
    ws_resumen[f'B{guia_row}'].font = font_section

    guias = [
        ("❌ Pestaña 'Faltas'", "Muestra ÚNICAMENTE a los comuneros que no asistieron (sin justificación). Ideal para pasar lista y aplicar multas comunales."),
        ("✅ Pestaña 'Asistieron'", "Muestra a los comuneros presentes con horas de ingreso y salida exactas."),
        ("📝 Pestaña 'Justificaciones'", "Muestra las inasistencias debidamente sustentadas y el motivo registrado."),
        ("🎖️ Pestaña 'Exonerados y Tardanzas'", "Socios exonerados de tercera edad y registros con observación de hora."),
        ("📋 Pestaña 'Padrón General'", "Base completa con Autofiltros activos para búsquedas y filtros personalizados."),
    ]

    for g_idx, (pest, desc) in enumerate(guias, guia_row + 1):
        ws_resumen.row_dimensions[g_idx].height = 20
        cp = ws_resumen[f'B{g_idx}']
        cp.value = pest
        cp.font = font_bold
        cp.border = border_soft

        cd = ws_resumen[f'C{g_idx}']
        cd.value = desc
        cd.font = font_data
        cd.border = border_soft
        ws_resumen.merge_cells(f'C{g_idx}:G{g_idx}')

    ws_resumen.column_dimensions['A'].width = 3
    ws_resumen.column_dimensions['B'].width = 32
    ws_resumen.column_dimensions['C'].width = 20
    ws_resumen.column_dimensions['D'].width = 18
    ws_resumen.column_dimensions['E'].width = 18
    ws_resumen.column_dimensions['F'].width = 18
    ws_resumen.column_dimensions['G'].width = 18


def _build_excel_matrix_sheet(ws_matrix, config, report_data, sorted_users):
    """
    Construye la Sábana / Matriz Horizontal de Asistencias:
    - Columnas fijas: N°, DNI, Apellidos y Nombres, Condición
    - Columnas por cada reunión con fecha y nombre
    - Columnas finales de totales: Total Reuniones, Asistencias, Faltas, Justificadas, % Asistencia, Estado Comunal
    - Freeze Panes en E6 (fija columnas A-D y fila de encabezados)
    - AutoFiltros en todos los encabezados
    """
    ws_matrix.title = "Matriz de Asambleas"
    ws_matrix.sheet_properties.tabColor = "0D9488"
    ws_matrix.views.sheetView[0].showGridLines = True

    font_banner_title = Font(name="Calibri", size=13, bold=True, color="0F172A")
    font_banner_sub = Font(name="Calibri", size=9, bold=True, color="64748B")
    font_header = Font(name="Calibri", size=9, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=9, color="0F172A")
    font_correlativo = Font(name="Calibri", size=9, color="64748B")

    fill_matrix_header = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
    fill_summary_header = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    fill_zebra_even = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    fill_zebra_odd = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    border_soft = Border(
        left=Side(style='thin', color="E2E8F0"),
        right=Side(style='thin', color="E2E8F0"),
        top=Side(style='thin', color="E2E8F0"),
        bottom=Side(style='thin', color="E2E8F0")
    )
    border_bottom_thick = Border(
        left=Side(style='thin', color="CBD5E1"),
        right=Side(style='thin', color="CBD5E1"),
        top=Side(style='thin', color="CBD5E1"),
        bottom=Side(style='medium', color="0F172A")
    )

    badge_styles = {
        'FALTA': {
            'fill': PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="991B1B"),
        },
        'ASISTIÓ': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="166534"),
        },
        'ASISTIO': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="166534"),
        },
        'EXONERADO': {
            'fill': PatternFill(start_color="F3E8FF", end_color="F3E8FF", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="6B21A8"),
        },
        'JUSTIFICADA': {
            'fill': PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="1E40AF"),
        },
        'PENDIENTE': {
            'fill': PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="92400E"),
        },
        'AL DÍA': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="166534"),
        },
        'AL DIA': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="166534"),
        },
        'OBSERVADO': {
            'fill': PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="92400E"),
        },
        'SANCIÓN': {
            'fill': PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="991B1B"),
        },
        'SANCION': {
            'fill': PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
            'font': Font(name="Calibri", size=9, bold=True, color="991B1B"),
        },
    }

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_header = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws_matrix.sheet_format.defaultRowHeight = 20
    ws_matrix.row_dimensions[1].height = 6
    ws_matrix.row_dimensions[2].height = 22
    ws_matrix.row_dimensions[3].height = 18
    ws_matrix.row_dimensions[4].height = 8
    ws_matrix.row_dimensions[5].height = 34

    inst_nombre = (config.nombre_institucion if config else "COMUNIDAD CAMPESINA DE QUIULACOCHA").upper()
    ws_matrix['A2'] = f"{inst_nombre} - SÁBANA / MATRIZ GENERAL DE ASISTENCIA"
    ws_matrix['A2'].font = font_banner_title
    ws_matrix['A3'] = f"Control horizontal de asistencias y faltas por comunero | Total Comuneros: {len(sorted_users)} | Total Eventos: {len(report_data)}"
    ws_matrix['A3'].font = font_banner_sub

    # Base headers
    headers = ['N°', 'DNI', 'APELLIDOS Y NOMBRES', 'CONDICIÓN']
    
    # Event headers
    event_list = [ev_data['evento'] for ev_data in report_data]
    for ev in event_list:
        tag = "[FAENA]" if getattr(ev, 'es_faena', False) else "[ASAMB]"
        ev_title = f"{tag} {ev.fecha.strftime('%d/%m/%y')}\n{ev.nombre[:16]}"
        headers.append(ev_title)
        
    # Summary columns
    headers.extend([
        'TOTAL REUNIONES',
        'ASISTENCIAS',
        'FALTAS TOTALES',
        'FALTAS ASAMBLEAS',
        'FALTAS FAENAS',
        'JUSTIFICADAS',
        '% ASISTENCIA',
        'ESTADO COMUNAL',
    ])

    # Write headers
    num_events = len(event_list)
    for col_idx, header in enumerate(headers, 1):
        cell = ws_matrix.cell(row=5, column=col_idx, value=header)
        cell.font = font_header
        if col_idx > 4 + num_events:
            cell.fill = fill_summary_header
        else:
            cell.fill = fill_matrix_header
        cell.alignment = align_header
        cell.border = border_bottom_thick

    # Write data rows
    curr_row = 6
    for idx, u in enumerate(sorted_users, 1):
        row_fill = fill_zebra_even if (idx % 2 == 0) else fill_zebra_odd
        col_idx = 1
        
        # 1. N°
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=idx)
        c.font = font_correlativo
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        col_idx += 1
        
        # 2. DNI
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u['dni'])
        c.font = font_data
        c.number_format = '@'
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        col_idx += 1
        
        # 3. Socio
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u['socio'])
        c.font = font_data
        c.alignment = align_left
        c.border = border_soft
        c.fill = row_fill
        col_idx += 1
        
        # 4. Condición
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u['condicion'])
        c.font = font_data
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        col_idx += 1
        
        # Event columns
        for ev in event_list:
            status = u['eventos'].get(ev.id, 'FALTA')
            c = ws_matrix.cell(row=curr_row, column=col_idx, value=status)
            c.border = border_soft
            c.alignment = align_center
            
            matched_badge = None
            for k, b in badge_styles.items():
                if k in status:
                    matched_badge = b
                    break
            if matched_badge:
                c.fill = matched_badge['fill']
                c.font = matched_badge['font']
            else:
                c.fill = row_fill
                c.font = font_data
            col_idx += 1
            
        # Summary metrics
        # Total reuniones
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u['total_eventos'])
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        c.font = font_data
        col_idx += 1
        
        # Asistencias
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u['asistencias'])
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        c.font = Font(name="Calibri", size=9, bold=True, color="166534")
        col_idx += 1
        
        # Faltas Totales
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u['faltas'])
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        c.font = Font(name="Calibri", size=9, bold=True, color="991B1B" if u['faltas'] > 0 else "64748B")
        col_idx += 1

        # Faltas Asambleas
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u.get('faltas_asambleas', 0))
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        c.font = Font(name="Calibri", size=9, bold=True, color="1E40AF" if u.get('faltas_asambleas', 0) > 0 else "64748B")
        col_idx += 1

        # Faltas Faenas
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u.get('faltas_faenas', 0))
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        c.font = Font(name="Calibri", size=9, bold=True, color="047857" if u.get('faltas_faenas', 0) > 0 else "64748B")
        col_idx += 1
        
        # Justificadas
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u['justificadas'])
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        c.font = font_data
        col_idx += 1
        
        # % Asistencia
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=f"{u['porcentaje_asistencia']:.1f}%")
        c.alignment = align_center
        c.border = border_soft
        c.fill = row_fill
        c.font = Font(name="Calibri", size=9, bold=True, color="0F172A")
        col_idx += 1
        
        # Estado Comunal
        c = ws_matrix.cell(row=curr_row, column=col_idx, value=u['estado_comunal'])
        c.alignment = align_center
        c.border = border_soft
        matched_badge = None
        for k, b in badge_styles.items():
            if k in u['estado_comunal']:
                matched_badge = b
                break
        if matched_badge:
            c.fill = matched_badge['fill']
            c.font = matched_badge['font']
        else:
            c.fill = row_fill
            c.font = font_data
        col_idx += 1

        curr_row += 1

    if len(sorted_users) == 0:
        c = ws_matrix.cell(row=curr_row, column=1, value="No se encontraron registros de comuneros para esta selección.")
        c.font = Font(name="Calibri", size=10, italic=True, color="64748B")
        curr_row += 1

    # Freeze Panes: Row 5 header and columns A, B, C, D (N°, DNI, Apellidos y Nombres, Condición)
    ws_matrix.freeze_panes = "E6"

    # AutoFilter across all headers
    last_col_letter = get_column_letter(len(headers))
    if len(sorted_users) > 0:
        ws_matrix.auto_filter.ref = f"A5:{last_col_letter}{curr_row - 1}"

    # Column dimensions
    ws_matrix.column_dimensions['A'].width = 6
    ws_matrix.column_dimensions['B'].width = 13
    ws_matrix.column_dimensions['C'].width = 34
    ws_matrix.column_dimensions['D'].width = 14
    for c_i in range(5, 5 + num_events):
        ws_matrix.column_dimensions[get_column_letter(c_i)].width = 16
    ws_matrix.column_dimensions[get_column_letter(5 + num_events)].width = 16
    ws_matrix.column_dimensions[get_column_letter(6 + num_events)].width = 14
    ws_matrix.column_dimensions[get_column_letter(7 + num_events)].width = 14
    ws_matrix.column_dimensions[get_column_letter(8 + num_events)].width = 16
    ws_matrix.column_dimensions[get_column_letter(9 + num_events)].width = 14
    ws_matrix.column_dimensions[get_column_letter(10 + num_events)].width = 14
    ws_matrix.column_dimensions[get_column_letter(11 + num_events)].width = 14
    ws_matrix.column_dimensions[get_column_letter(12 + num_events)].width = 24


def generate_attendance_excel(unified_list, filename="asistencias.xlsx", system_config=None, titulo=None):
    """
    Genera un archivo Excel Ejecutivo y Diversificado con múltiples pestañas temáticas:
    - Resumen Ejecutivo (KPIs, porcentajes y guía)
    - Faltas (Inasistencias directas sin justificación para control comunal)
    - Asistieron (Asistencias físicas con horas)
    - Justificaciones (Permisos aprobados con motivo)
    - Exonerados y Tardanzas (Casos especiales y de tercera edad)
    - Padrón General (Consolidado total con autofiltros y freeze panes)
    """
    wb = Workbook()
    
    config = system_config or ConfiguracionSistema.objects.first()
    tardanza_activa = bool(config and config.tardanza_activa)
    inst_nombre = (config.nombre_institucion if config else "COMUNIDAD CAMPESINA DE QUIULACOCHA").upper()
    fecha_descarga = datetime.now().strftime('%d/%m/%Y %H:%M')

    # Detectar evento predominante para encabezados
    primer_evento = next((getattr(it, 'evento', None) for it in unified_list if getattr(it, 'evento', None)), None)
    evento_nombre = titulo or (primer_evento.nombre if primer_evento else "General / Filtrado")

    # Normalizar todos los registros
    records = [_normalize_attendance_record(it, tardanza_activa) for it in unified_list]

    # Clasificar en listas para pestañas
    records_faltas = [r for r in records if r['categoria'] == 'FALTA']
    records_asistieron = [r for r in records if r['categoria'] == 'ASISTIO']
    records_justificadas = [r for r in records if r['categoria'] == 'JUSTIFICADA']
    records_pendientes = [r for r in records if r['categoria'] == 'PENDIENTE']
    records_exonerados = [
        r for r in records 
        if r['condicion_socio'] == 'Exonerado' or getattr(r['usuario'], 'estado', None) == Usuario.ESTADO_EXONERADO
    ]

    total_padron = len(records)
    total_asistieron = len(records_asistieron)
    total_justificadas = len(records_justificadas)
    total_faltas = len(records_faltas)
    total_pendientes = len(records_pendientes)
    total_exonerados = len(records_exonerados)
    total_presentes_efectivos = total_asistieron + total_justificadas
    porcentaje_asistencia = (total_presentes_efectivos / total_padron * 100) if total_padron > 0 else 0

    stats = {
        'total_padron': total_padron,
        'total_asistieron': total_asistieron,
        'total_justificadas': total_justificadas,
        'total_faltas': total_faltas,
        'total_pendientes': total_pendientes,
        'total_exonerados': total_exonerados,
        'porcentaje_asistencia': porcentaje_asistencia,
    }

    # 1. Pestaña: Resumen Ejecutivo
    ws_resumen = wb.active
    _build_excel_summary_sheet(ws_resumen, config, evento_nombre, fecha_descarga, stats)

    # 2. Pestaña: Consolidado por Comunero (Récord y balance individual por socio)
    user_grouped = {}
    for r in records:
        dni = r['dni']
        if dni not in user_grouped:
            user_grouped[dni] = {
                'dni': dni,
                'nombre_completo': r['nombre_completo'],
                'condicion': r['condicion_socio'],
                'total_convocadas': 0,
                'asistencias': 0,
                'faltas': 0,
                'faltas_asambleas': 0,
                'faltas_faenas': 0,
                'justificadas': 0,
                'pendientes': 0,
                'reuniones_faltadas': [],
                'faenas_faltadas': [],
                'asambleas_faltadas': [],
            }
        user_grouped[dni]['total_convocadas'] += 1
        cat = r['categoria']
        ev_str = f"{r['fecha_str']} ({r['evento_str']})" if r['evento_str'] else r['fecha_str']
        is_faena = bool(r.get('es_faena'))

        if cat == 'FALTA':
            user_grouped[dni]['faltas'] += 1
            user_grouped[dni]['reuniones_faltadas'].append(ev_str)
            if is_faena:
                user_grouped[dni]['faltas_faenas'] += 1
                user_grouped[dni]['faenas_faltadas'].append(ev_str)
            else:
                user_grouped[dni]['faltas_asambleas'] += 1
                user_grouped[dni]['asambleas_faltadas'].append(ev_str)
        elif cat == 'JUSTIFICADA':
            user_grouped[dni]['justificadas'] += 1
        elif cat == 'PENDIENTE':
            user_grouped[dni]['pendientes'] += 1
            user_grouped[dni]['asistencias'] += 1
        else:
            user_grouped[dni]['asistencias'] += 1

    sorted_user_grouped = sorted(user_grouped.values(), key=lambda x: str(x['nombre_completo']).upper())
    rows_user_grouped = []
    for i, ug in enumerate(sorted_user_grouped, 1):
        presentes = ug['asistencias'] + ug['justificadas']
        total_conv = ug['total_convocadas']
        pct = (presentes / total_conv * 100) if total_conv > 0 else 0
        if str(ug['condicion']).upper() == 'EXONERADO':
            estado_comunal = 'EXONERADO'
        elif ug['faltas'] == 0:
            estado_comunal = 'AL DÍA (0 FALTAS)'
        elif ug['faltas'] <= 2:
            estado_comunal = f'OBSERVADO ({ug["faltas"]} FALTAS)'
        else:
            estado_comunal = f'CON SANCIÓN ({ug["faltas"]} FALTAS)'

        faenas_detalle = " | ".join(ug['faenas_faltadas']) if ug['faenas_faltadas'] else "Ninguna (0 faltas a faenas)"
        asambleas_detalle = " | ".join(ug['asambleas_faltadas']) if ug['asambleas_faltadas'] else "Ninguna (0 faltas a asambleas)"
        faltas_detalle = " | ".join(ug['reuniones_faltadas']) if ug['reuniones_faltadas'] else "Ninguna (Asistencia completa)"

        rows_user_grouped.append([
            i,
            ug['dni'],
            ug['nombre_completo'],
            ug['condicion'],
            total_conv,
            ug['asistencias'],
            ug['faltas'],
            ug['faltas_asambleas'],
            ug['faltas_faenas'],
            ug['justificadas'],
            f"{pct:.1f}%",
            estado_comunal,
            faenas_detalle,
            asambleas_detalle,
            faltas_detalle,
        ])

    ws_consolidado = wb.create_sheet("Consolidado por Comunero")
    headers_consolidado = [
        'N°',
        'DNI',
        'APELLIDOS Y NOMBRES',
        'CONDICIÓN',
        'TOTAL EVENTOS',
        'ASISTENCIAS',
        'FALTAS TOTALES',
        'FALTAS A ASAMBLEAS',
        'FALTAS A FAENAS',
        'JUSTIFICADAS',
        '% ASISTENCIA',
        'ESTADO COMUNAL',
        'FAENAS EN QUE FALTÓ',
        'ASAMBLEAS EN QUE FALTÓ',
        'DETALLE COMPLETO DE FALTAS',
    ]
    fill_consolidado = PatternFill(start_color="312E81", end_color="312E81", fill_type="solid")
    _build_excel_data_sheet(
        ws_consolidado,
        sheet_title="Consolidado por Comunero",
        tab_color="4F46E5",
        banner_title=f"{inst_nombre} - RESUMEN CONSOLIDADO POR COMUNERO",
        banner_subtitle=f"Filtro: {evento_nombre} | Total comuneros: {len(sorted_user_grouped)}",
        headers=headers_consolidado,
        rows_data=rows_user_grouped,
        header_fill=fill_consolidado,
    )

    # 3. Pestaña: Faltas a Faenas
    records_faltas_faenas = [r for r in records_faltas if r.get('es_faena')]
    ws_faltas_faenas = wb.create_sheet("Faltas a Faenas")
    headers_faenas = ['N°', 'APELLIDOS Y NOMBRES', 'DNI', 'CONDICIÓN', 'FECHA', 'FAENA COMUNAL', 'ESTADO', 'OBSERVACIÓN']
    rows_faenas = [
        [
            i,
            r['nombre_completo'],
            r['dni'],
            r['condicion_socio'],
            r['fecha_str'],
            r['evento_str'],
            r['estado_texto'],
            r['observacion'],
        ]
        for i, r in enumerate(records_faltas_faenas, 1)
    ]
    fill_faenas = PatternFill(start_color="047857", end_color="047857", fill_type="solid")
    _build_excel_data_sheet(
        ws_faltas_faenas,
        sheet_title="Faltas a Faenas",
        tab_color="047857",
        banner_title=f"{inst_nombre} - RELACIÓN DE FALTAS A FAENAS COMUNALES",
        banner_subtitle=f"Filtro: {evento_nombre} | Total Faltas a Faenas: {len(records_faltas_faenas)} casos",
        headers=headers_faenas,
        rows_data=rows_faenas,
        header_fill=fill_faenas,
    )

    # 4. Pestaña: Faltas a Asambleas
    records_faltas_asambleas = [r for r in records_faltas if not r.get('es_faena')]
    ws_faltas_asambleas = wb.create_sheet("Faltas a Asambleas")
    headers_asambleas = ['N°', 'APELLIDOS Y NOMBRES', 'DNI', 'CONDICIÓN', 'FECHA', 'ASAMBLEA', 'ESTADO', 'OBSERVACIÓN']
    rows_asambleas = [
        [
            i,
            r['nombre_completo'],
            r['dni'],
            r['condicion_socio'],
            r['fecha_str'],
            r['evento_str'],
            r['estado_texto'],
            r['observacion'],
        ]
        for i, r in enumerate(records_faltas_asambleas, 1)
    ]
    fill_asambleas = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    _build_excel_data_sheet(
        ws_faltas_asambleas,
        sheet_title="Faltas a Asambleas",
        tab_color="1E40AF",
        banner_title=f"{inst_nombre} - RELACIÓN DE FALTAS A ASAMBLEAS",
        banner_subtitle=f"Filtro: {evento_nombre} | Total Faltas a Asambleas: {len(records_faltas_asambleas)} casos",
        headers=headers_asambleas,
        rows_data=rows_asambleas,
        header_fill=fill_asambleas,
    )

    # 3. Pestaña: Faltas Reales
    ws_faltas = wb.create_sheet("Faltas Reales")
    headers_faltas = ['N°', 'APELLIDOS Y NOMBRES', 'DNI', 'CONDICIÓN', 'FECHA', 'EVENTO', 'ESTADO', 'OBSERVACIÓN']
    rows_faltas = [
        [
            i,
            r['nombre_completo'],
            r['dni'],
            r['condicion_socio'],
            r['fecha_str'],
            r['evento_str'],
            r['estado_texto'],
            r['observacion'],
        ]
        for i, r in enumerate(records_faltas, 1)
    ]
    fill_faltas = PatternFill(start_color="991B1B", end_color="991B1B", fill_type="solid")
    _build_excel_data_sheet(
        ws_faltas,
        sheet_title="Faltas Reales",
        tab_color="991B1B",
        banner_title=f"{inst_nombre} - RELACIÓN DE FALTAS REALES (INASISTENCIAS)",
        banner_subtitle=f"Evento: {evento_nombre} | Total Faltas: {len(records_faltas)} socios",
        headers=headers_faltas,
        rows_data=rows_faltas,
        header_fill=fill_faltas,
    )

    # 3. Pestaña: Asistieron
    ws_asistieron = wb.create_sheet("Asistieron")
    headers_asistieron = ['N°', 'APELLIDOS Y NOMBRES', 'DNI', 'CONDICIÓN', 'FECHA', 'INGRESO', 'SALIDA', 'PUNTUALIDAD', 'LUGAR', 'ESTADO']
    rows_asistieron = [
        [
            i,
            r['nombre_completo'],
            r['dni'],
            r['condicion_socio'],
            r['fecha_str'],
            r['hora_ingreso_str'],
            r['hora_salida_str'],
            r['puntualidad_texto'],
            r['ubicacion_str'],
            r['estado_texto'],
        ]
        for i, r in enumerate(records_asistieron, 1)
    ]
    fill_asistieron = PatternFill(start_color="065F46", end_color="065F46", fill_type="solid")
    _build_excel_data_sheet(
        ws_asistieron,
        sheet_title="Asistieron",
        tab_color="065F46",
        banner_title=f"{inst_nombre} - RELACIÓN DE ASISTENCIAS CONFIRMADAS",
        banner_subtitle=f"Evento: {evento_nombre} | Total Asistieron: {len(records_asistieron)} socios",
        headers=headers_asistieron,
        rows_data=rows_asistieron,
        header_fill=fill_asistieron,
    )

    # 4. Pestaña: Justificaciones
    ws_justificadas = wb.create_sheet("Justificaciones")
    headers_justificadas = ['N°', 'APELLIDOS Y NOMBRES', 'DNI', 'CONDICIÓN', 'FECHA', 'EVENTO', 'ESTADO', 'MOTIVO DE JUSTIFICACIÓN']
    rows_justificadas = [
        [
            i,
            r['nombre_completo'],
            r['dni'],
            r['condicion_socio'],
            r['fecha_str'],
            r['evento_str'],
            r['estado_texto'],
            r['observacion'],
        ]
        for i, r in enumerate(records_justificadas, 1)
    ]
    fill_justificadas = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    _build_excel_data_sheet(
        ws_justificadas,
        sheet_title="Justificaciones",
        tab_color="1E40AF",
        banner_title=f"{inst_nombre} - INASISTENCIAS JUSTIFICADAS CON SUSTENTO",
        banner_subtitle=f"Evento: {evento_nombre} | Total Justificadas: {len(records_justificadas)} socios",
        headers=headers_justificadas,
        rows_data=rows_justificadas,
        header_fill=fill_justificadas,
    )

    # 5. Pestaña: Pendientes
    ws_pendientes = wb.create_sheet("Pendientes")
    headers_pendientes = ['N°', 'APELLIDOS Y NOMBRES', 'DNI', 'CONDICIÓN', 'FECHA', 'INGRESO', 'SALIDA', 'ESTADO', 'OBSERVACIÓN']
    rows_pendientes = [
        [
            i,
            r['nombre_completo'],
            r['dni'],
            r['condicion_socio'],
            r['fecha_str'],
            r['hora_ingreso_str'],
            r['hora_salida_str'],
            r['estado_texto'],
            r['observacion'],
        ]
        for i, r in enumerate(records_pendientes, 1)
    ]
    fill_pendientes = PatternFill(start_color="D97706", end_color="D97706", fill_type="solid")
    _build_excel_data_sheet(
        ws_pendientes,
        sheet_title="Pendientes",
        tab_color="D97706",
        banner_title=f"{inst_nombre} - REGISTROS CON SALIDA PENDIENTE",
        banner_subtitle=f"Evento: {evento_nombre} | Total Pendientes: {len(records_pendientes)} socios",
        headers=headers_pendientes,
        rows_data=rows_pendientes,
        header_fill=fill_pendientes,
    )

    # 6. Pestaña: Exonerados
    ws_exonerados = wb.create_sheet("Exonerados")
    headers_exonerados = ['N°', 'APELLIDOS Y NOMBRES', 'DNI', 'CONDICIÓN', 'FECHA', 'INGRESO', 'SALIDA', 'ESTADO', 'OBSERVACIÓN']
    rows_exonerados = [
        [
            i,
            r['nombre_completo'],
            r['dni'],
            r['condicion_socio'],
            r['fecha_str'],
            r['hora_ingreso_str'],
            r['hora_salida_str'],
            r['estado_texto'],
            r['observacion'],
        ]
        for i, r in enumerate(records_exonerados, 1)
    ]
    fill_exonerados = PatternFill(start_color="6B21A8", end_color="6B21A8", fill_type="solid")
    _build_excel_data_sheet(
        ws_exonerados,
        sheet_title="Exonerados",
        tab_color="6B21A8",
        banner_title=f"{inst_nombre} - COMUNEROS EXONERADOS (TERCERA EDAD)",
        banner_subtitle=f"Evento: {evento_nombre} | Total Exonerados: {len(records_exonerados)} socios",
        headers=headers_exonerados,
        rows_data=rows_exonerados,
        header_fill=fill_exonerados,
    )

    # 7. Pestaña: Padrón General
    ws_general = wb.create_sheet("Padrón General")
    headers_general = ['N°', 'APELLIDOS Y NOMBRES', 'DNI', 'CONDICIÓN', 'EVENTO', 'FECHA', 'INGRESO', 'SALIDA', 'ESTADO GENERAL', 'PUNTUALIDAD', 'OBSERVACIONES']
    rows_general = [
        [
            i,
            r['nombre_completo'],
            r['dni'],
            r['condicion_socio'],
            r['evento_str'],
            r['fecha_str'],
            r['hora_ingreso_str'],
            r['hora_salida_str'],
            r['estado_texto'],
            r['puntualidad_texto'],
            r['observacion'],
        ]
        for i, r in enumerate(records, 1)
    ]
    fill_general = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    _build_excel_data_sheet(
        ws_general,
        sheet_title="Padrón General",
        tab_color="334155",
        banner_title=f"{inst_nombre} - CONSOLIDADO GENERAL DE ASISTENCIA",
        banner_subtitle=f"Evento: {evento_nombre} | Base completa con filtros automáticos activos",
        headers=headers_general,
        rows_data=rows_general,
        header_fill=fill_general,
        is_general=True,
    )

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

    weasyprint_error = None
    if HTML is not None:
        try:
            print(f"Generando PDF con WeasyPrint: {filename}")
            pdf_file = HTML(string=html_string).write_pdf()
            response = HttpResponse(pdf_file, content_type='application/pdf')
            response['Content-Disposition'] = f'inline; filename="{filename}"'
            response['Content-Length'] = len(pdf_file)
            response['X-Content-Type-Options'] = 'nosniff'
            return response
        except Exception as exc:
            weasyprint_error = exc
            print(f"WeasyPrint falló, intentando fallback con xhtml2pdf: {str(exc)}")
    else:
        weasyprint_error = RuntimeError("WeasyPrint no está disponible en este entorno.")
        print("WeasyPrint no está disponible, intentando fallback con xhtml2pdf.")

    if pisa is not None:
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

    return HttpResponse(f"Error al generar el reporte: {str(weasyprint_error)}", status=500)

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
    Genera un Excel Consolidado Anual con múltiples pestañas temáticas:
    - Resumen Ejecutivo (Balance de eventos anuales y KPIs)
    - Inasistencias Acumuladas (Todas las faltas del año consolidadas para revisión rápida)
    - Justificaciones Acumuladas (Historial de permisos y sustentos)
    - Detalle General (Histórico completo con autofiltros y freeze panes)
    """
    from io import BytesIO
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    
    # --- HOJA 1: RESUMEN EJECUTIVO ---
    ws_summary = wb.active
    ws_summary.title = "Resumen Ejecutivo"
    ws_summary.sheet_properties.tabColor = "1E293B"
    ws_summary.views.sheetView[0].showGridLines = True
    
    # Estilos Premium
    indigo_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    slate_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    white_font = Font(bold=True, color="FFFFFF")
    title_font = Font(bold=True, size=15, color="0F172A")
    subtitle_font = Font(bold=True, size=11, color="334155")
    border_thin = Border(
        left=Side(style='thin', color="E2E8F0"), 
        right=Side(style='thin', color="E2E8F0"), 
        top=Side(style='thin', color="E2E8F0"), 
        bottom=Side(style='thin', color="E2E8F0")
    )
    fill_even = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    # Título Principal
    ws_summary.merge_cells('B2:G2')
    cell_title = ws_summary['B2']
    cell_title.value = (system_config.nombre_institucion if system_config else "SISTEMA DE ASISTENCIA").upper()
    cell_title.font = title_font
    cell_title.alignment = Alignment(horizontal="center")

    ws_summary.merge_cells('B3:G3')
    cell_subtitle = ws_summary['B3']
    cell_subtitle.value = f"REPORTE GLOBAL ANUAL CONSOLIDADO - {datetime.now().year}"
    cell_subtitle.font = subtitle_font
    cell_subtitle.alignment = Alignment(horizontal="center")

    # Tabla de Totales Generales
    ws_summary['B5'] = "MÉTRICA GLOBAL ANUAL"
    ws_summary['C5'] = "VALOR CONSOLIDADO"
    for cell in [ws_summary['B5'], ws_summary['C5']]:
        cell.fill = slate_fill
        cell.font = white_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border_thin

    total_eventos = len(report_data)
    total_padron_acumulado = sum(ev['stats']['total_usuarios'] for ev in report_data)
    total_asistencias_fisicas_acumulado = sum(ev['stats']['asistencias_fisicas'] for ev in report_data)
    total_justificadas_acumulado = sum(ev['stats']['justificadas'] for ev in report_data)
    total_inasistencias_acumuladas = sum(ev['stats']['inasistencias'] for ev in report_data)
    total_presentes_acumulado = total_asistencias_fisicas_acumulado + total_justificadas_acumulado
    promedio_asistencia = (total_presentes_acumulado / total_padron_acumulado * 100) if total_padron_acumulado > 0 else 0
    total_faenas = sum(1 for ev in report_data if getattr(ev['evento'], 'es_faena', False))
    total_asambleas = total_eventos - total_faenas

    metrics = [
        ("Total Eventos Realizados", total_eventos),
        ("Total Asambleas Convocadas", total_asambleas),
        ("Total Faenas Comunales", total_faenas),
        ("Padrón Convocado (Acumulado)", total_padron_acumulado),
        ("Asistencias Físicas Efectivas", total_asistencias_fisicas_acumulado),
        ("Inasistencias / Faltas Acumuladas", total_inasistencias_acumuladas),
        ("Justificadas Totales", total_justificadas_acumulado),
        ("Presentes* (Físicas + Justificadas)", total_presentes_acumulado),
        ("Promedio General de Asistencia", f"{promedio_asistencia:.1f}%")
    ]

    for i, (m, v) in enumerate(metrics, start=6):
        ws_summary.cell(row=i, column=2, value=m).border = border_thin
        ws_summary.cell(row=i, column=3, value=v).border = border_thin
        ws_summary.cell(row=i, column=3).alignment = Alignment(horizontal="center", vertical="center")

    # Tabla de Detalle por Evento en el Resumen
    start_row_events = 17
    headers_resumen_ev = ["EVENTO", "TIPO", "FECHA", "PADRÓN", "PRESENTES*", "FALTAS", "JUSTIFICADAS", "% ASISTENCIA"]
    for col_idx, h_text in enumerate(headers_resumen_ev, 2):
        cell_h = ws_summary.cell(row=start_row_events, column=col_idx, value=h_text)
        cell_h.fill = indigo_fill
        cell_h.font = white_font
        cell_h.alignment = Alignment(horizontal="center", vertical="center")

    for i, ev_data in enumerate(report_data, 1):
        row = start_row_events + i
        ws_summary.row_dimensions[row].height = 20
        row_fill = fill_even if (i % 2 == 0) else None
        
        c2 = ws_summary.cell(row=row, column=2, value=ev_data['evento'].nombre)
        c3 = ws_summary.cell(row=row, column=3, value=ev_data['evento'].tipo_nombre)
        c4 = ws_summary.cell(row=row, column=4, value=ev_data['evento'].fecha.strftime('%d/%m/%Y'))
        c5 = ws_summary.cell(row=row, column=5, value=ev_data['stats']['total_usuarios'])
        c6 = ws_summary.cell(row=row, column=6, value=ev_data['stats']['asistencias_fisicas'])
        c7 = ws_summary.cell(row=row, column=7, value=ev_data['stats']['inasistencias'])
        c8 = ws_summary.cell(row=row, column=8, value=ev_data['stats']['justificadas'])
        c9 = ws_summary.cell(row=row, column=9, value=f"{ev_data['stats']['porcentaje']:.1f}%")
        
        for c in [c2, c3, c4, c5, c6, c7, c8, c9]:
            c.border = border_thin
            if row_fill:
                c.fill = row_fill
        for c in [c3, c4, c5, c6, c7, c8, c9]:
            c.alignment = Alignment(horizontal="center", vertical="center")

    ws_summary.column_dimensions['B'].width = 35
    ws_summary.column_dimensions['C'].width = 22
    ws_summary.column_dimensions['D'].width = 14
    ws_summary.column_dimensions['E'].width = 12
    ws_summary.column_dimensions['F'].width = 15
    ws_summary.column_dimensions['G'].width = 15
    ws_summary.column_dimensions['H'].width = 15
    ws_summary.column_dimensions['I'].width = 16

    # --- RECOPILACIÓN PARA PESTAÑAS ESPECIALIZADAS ---
    faltas_globales = []
    faltas_faenas_globales = []
    faltas_asambleas_globales = []
    justificadas_globales = []
    detalle_global = []
    user_records = {}

    for ev_data in report_data:
        ev = ev_data['evento']
        ev_nombre = ev.nombre
        ev_fecha = ev.fecha.strftime('%d/%m/%Y')
        is_faena_ev = getattr(ev, 'es_faena', False)
        for rec in ev_data['records']:
            if isinstance(rec, dict):
                usuario = rec['usuario']
                is_absent = rec.get('is_absent', False)
                es_justificada = rec.get('es_justificada', False)
                is_late_absent = rec.get('is_late_absent', False)
                hora_ingreso = rec.get('hora_ingreso')
                hora_salida = rec.get('hora_salida')
                obs_raw = rec.get('justificacion_obs', "")
            else:
                usuario = _extract_attendance_user(rec)
                tardanza_activa = bool(system_config and getattr(system_config, 'tardanza_activa', True))
                flags = classify_attendance_item(rec, tardanza_activa)
                is_absent = flags.is_absent
                es_justificada = bool(getattr(rec, 'es_justificada', False))
                is_late_absent = flags.is_late_absent
                hora_ingreso = getattr(rec, 'hora_ingreso', None)
                hora_salida = getattr(rec, 'hora_salida', None)
                obs_raw = getattr(rec, 'justificacion_obs', "")

            dni = str(usuario.dni)
            if dni not in user_records:
                user_records[dni] = {
                    'usuario': usuario,
                    'dni': dni,
                    'socio': f"{usuario.apellido}, {usuario.nombre}" if (usuario.apellido and usuario.nombre) else (usuario.nombre or usuario.dni),
                    'apellido': (usuario.apellido or '').strip(),
                    'nombre': (usuario.nombre or '').strip(),
                    'condicion': usuario.get_estado_display() if hasattr(usuario, 'get_estado_display') else str(getattr(usuario, 'estado', '')),
                    'asistencias': 0,
                    'faltas': 0,
                    'faltas_asambleas': 0,
                    'faltas_faenas': 0,
                    'justificadas': 0,
                    'tardanzas': 0,
                    'pendientes': 0,
                    'exonerados': 0,
                    'reuniones_faltadas': [],
                    'faenas_faltadas': [],
                    'asambleas_faltadas': [],
                    'eventos': {},
                }
            
            # Determinar estado
            if is_absent:
                status_text = "JUSTIFICADA" if es_justificada else "FALTA"
                if es_justificada:
                    user_records[dni]['justificadas'] += 1
                else:
                    user_records[dni]['faltas'] += 1
                    ev_faltada_str = f"{ev_fecha} ({ev_nombre[:18]})"
                    user_records[dni]['reuniones_faltadas'].append(ev_faltada_str)
                    if is_faena_ev:
                        user_records[dni]['faltas_faenas'] += 1
                        user_records[dni]['faenas_faltadas'].append(ev_faltada_str)
                    else:
                        user_records[dni]['faltas_asambleas'] += 1
                        user_records[dni]['asambleas_faltadas'].append(ev_faltada_str)
            elif getattr(usuario, 'estado', None) == Usuario.ESTADO_EXONERADO:
                status_text = "ASISTIÓ (EXONERADO)"
                user_records[dni]['exonerados'] += 1
                user_records[dni]['asistencias'] += 1
            elif not hora_salida:
                status_text = "PENDIENTE"
                user_records[dni]['pendientes'] += 1
                user_records[dni]['asistencias'] += 1
            else:
                status_text = "ASISTIÓ"
                user_records[dni]['asistencias'] += 1

            if is_late_absent:
                user_records[dni]['tardanzas'] += 1

            user_records[dni]['eventos'][ev.id] = status_text

            if getattr(usuario, 'estado', None) == Usuario.ESTADO_EXONERADO and not is_absent:
                ingreso_str = "EXONERADO"
                salida_str = "EXONERADO"
            else:
                ingreso_str = hora_ingreso.strftime('%H:%M:%S') if hora_ingreso else "--"
                salida_str = hora_salida.strftime('%H:%M:%S') if hora_salida else "--"

            obs = obs_raw
            if is_late_absent:
                obs = "TARDE" if not obs else f"{obs} | TARDE"

            row_data = {
                'evento': ev_nombre,
                'tipo_evento': ev.tipo_nombre,
                'fecha': ev_fecha,
                'socio': f"{usuario.nombre} {usuario.apellido}",
                'dni': str(usuario.dni),
                'condicion': usuario.get_estado_display() if hasattr(usuario, 'get_estado_display') else str(getattr(usuario, 'estado', '')),
                'estado': status_text,
                'ingreso': ingreso_str,
                'salida': salida_str,
                'observacion': obs,
            }
            detalle_global.append(row_data)

            if is_absent and not es_justificada:
                faltas_globales.append(row_data)
                if is_faena_ev:
                    faltas_faenas_globales.append(row_data)
                else:
                    faltas_asambleas_globales.append(row_data)
            elif es_justificada:
                justificadas_globales.append(row_data)

    # Procesar métricas por comunero
    sorted_users = sorted(user_records.values(), key=lambda u: (u['apellido'].upper(), u['nombre'].upper(), u['dni']))
    for u in sorted_users:
        u['total_eventos'] = total_eventos
        presentes_totales = u['asistencias'] + u['justificadas']
        u['porcentaje_asistencia'] = (presentes_totales / total_eventos * 100) if total_eventos > 0 else 0
        if u['condicion'].upper() == 'EXONERADO' or getattr(u['usuario'], 'estado', None) == Usuario.ESTADO_EXONERADO:
            u['estado_comunal'] = 'EXONERADO'
        elif u['faltas'] == 0:
            u['estado_comunal'] = 'AL DÍA (0 FALTAS)'
        elif u['faltas'] <= 2:
            u['estado_comunal'] = f'OBSERVADO ({u["faltas"]} FALTAS)'
        else:
            u['estado_comunal'] = f'CON SANCIÓN ({u["faltas"]} FALTAS)'

    # --- HOJA 2: MATRIZ GENERAL DE ASAMBLEAS (SÁBANA DE CONTROL) ---
    ws_matrix = wb.create_sheet("Matriz de Asambleas")
    _build_excel_matrix_sheet(ws_matrix, system_config, report_data, sorted_users)

    # --- HOJA 3: CONSOLIDADO POR COMUNERO ---
    ws_user_summary = wb.create_sheet("Consolidado por Comunero")
    headers_rc = [
        'N°',
        'DNI',
        'APELLIDOS Y NOMBRES',
        'CONDICIÓN',
        'TOTAL REUNIONES',
        'ASISTENCIAS',
        'FALTAS TOTALES',
        'FALTAS A ASAMBLEAS',
        'FALTAS A FAENAS',
        'JUSTIFICADAS',
        'TARDANZAS',
        '% ASISTENCIA',
        'ESTADO COMUNAL',
        'FAENAS EN QUE FALTÓ',
        'ASAMBLEAS EN QUE FALTÓ',
        'TODAS LAS REUNIONES EN QUE FALTÓ',
    ]
    rows_rc = [
        [
            i,
            u['dni'],
            u['socio'],
            u['condicion'],
            u['total_eventos'],
            u['asistencias'],
            u['faltas'],
            u['faltas_asambleas'],
            u['faltas_faenas'],
            u['justificadas'],
            u['tardanzas'],
            f"{u['porcentaje_asistencia']:.1f}%",
            u['estado_comunal'],
            " | ".join(u['faenas_faltadas']) if u['faenas_faltadas'] else "Ninguna (0 faltas a faenas)",
            " | ".join(u['asambleas_faltadas']) if u['asambleas_faltadas'] else "Ninguna (0 faltas a asambleas)",
            " | ".join(u['reuniones_faltadas']) if u['reuniones_faltadas'] else "Ninguna (Asistencia completa)",
        ]
        for i, u in enumerate(sorted_users, 1)
    ]
    fill_rc = PatternFill(start_color="312E81", end_color="312E81", fill_type="solid")
    _build_excel_data_sheet(
        ws_user_summary,
        sheet_title="Consolidado por Comunero",
        tab_color="4F46E5",
        banner_title=f"{(system_config.nombre_institucion if system_config else 'QUIULACOCHA').upper()} - CONSOLIDADO ANUAL POR COMUNERO",
        banner_subtitle=f"Récord anual consolidado con desglose de Faenas y Asambleas | Total comuneros: {len(sorted_users)}",
        headers=headers_rc,
        rows_data=rows_rc,
        header_fill=fill_rc,
    )

    # --- HOJA 4: INASISTENCIAS ACUMULADAS ---
    ws_faltas_global = wb.create_sheet("Inasistencias Acumuladas")
    headers_fg = ['N°', 'EVENTO', 'TIPO', 'FECHA', 'SOCIO', 'DNI', 'CONDICIÓN', 'ESTADO', 'OBSERVACIÓN']
    rows_fg = [
        [
            i,
            r['evento'],
            r.get('tipo_evento', 'General'),
            r['fecha'],
            r['socio'],
            r['dni'],
            r['condicion'],
            r['estado'],
            r['observacion'],
        ]
        for i, r in enumerate(faltas_globales, 1)
    ]
    fill_fg = PatternFill(start_color="991B1B", end_color="991B1B", fill_type="solid")
    _build_excel_data_sheet(
        ws_faltas_global,
        sheet_title="Inasistencias Acumuladas",
        tab_color="991B1B",
        banner_title=f"{(system_config.nombre_institucion if system_config else 'QUIULACOCHA').upper()} - TODAS LAS INASISTENCIAS ANUALES ACUMULADAS",
        banner_subtitle=f"Histórico consolidado anual | Total inasistencias: {len(faltas_globales)}",
        headers=headers_fg,
        rows_data=rows_fg,
        header_fill=fill_fg,
    )

    # --- HOJA 5: FALTAS A FAENAS ---
    ws_faltas_faenas = wb.create_sheet("Faltas a Faenas")
    headers_ff = ['N°', 'FAENA COMUNAL', 'FECHA', 'SOCIO', 'DNI', 'CONDICIÓN', 'ESTADO', 'OBSERVACIÓN']
    rows_ff = [
        [
            i,
            r['evento'],
            r['fecha'],
            r['socio'],
            r['dni'],
            r['condicion'],
            r['estado'],
            r['observacion'],
        ]
        for i, r in enumerate(faltas_faenas_globales, 1)
    ]
    fill_ff = PatternFill(start_color="047857", end_color="047857", fill_type="solid")
    _build_excel_data_sheet(
        ws_faltas_faenas,
        sheet_title="Faltas a Faenas",
        tab_color="047857",
        banner_title=f"{(system_config.nombre_institucion if system_config else 'QUIULACOCHA').upper()} - INASISTENCIAS A FAENAS COMUNALES",
        banner_subtitle=f"Control estricto de trabajos comunales | Total inasistencias a faenas: {len(faltas_faenas_globales)}",
        headers=headers_ff,
        rows_data=rows_ff,
        header_fill=fill_ff,
    )

    # --- HOJA 6: FALTAS A ASAMBLEAS ---
    ws_faltas_asambleas = wb.create_sheet("Faltas a Asambleas")
    headers_fa = ['N°', 'ASAMBLEA', 'FECHA', 'SOCIO', 'DNI', 'CONDICIÓN', 'ESTADO', 'OBSERVACIÓN']
    rows_fa = [
        [
            i,
            r['evento'],
            r['fecha'],
            r['socio'],
            r['dni'],
            r['condicion'],
            r['estado'],
            r['observacion'],
        ]
        for i, r in enumerate(faltas_asambleas_globales, 1)
    ]
    fill_fa = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    _build_excel_data_sheet(
        ws_faltas_asambleas,
        sheet_title="Faltas a Asambleas",
        tab_color="1E40AF",
        banner_title=f"{(system_config.nombre_institucion if system_config else 'QUIULACOCHA').upper()} - INASISTENCIAS A ASAMBLEAS COMUNALES",
        banner_subtitle=f"Control de asambleas ordinarias y extraordinarias | Total inasistencias a asambleas: {len(faltas_asambleas_globales)}",
        headers=headers_fa,
        rows_data=rows_fa,
        header_fill=fill_fa,
    )

    # --- HOJA 5: JUSTIFICACIONES ACUMULADAS ---
    ws_just_global = wb.create_sheet("Justificaciones Acumuladas")
    headers_jg = ['N°', 'EVENTO', 'FECHA', 'SOCIO', 'DNI', 'CONDICIÓN', 'ESTADO', 'MOTIVO DE JUSTIFICACIÓN']
    rows_jg = [
        [
            i,
            r['evento'],
            r['fecha'],
            r['socio'],
            r['dni'],
            r['condicion'],
            r['estado'],
            r['observacion'],
        ]
        for i, r in enumerate(justificadas_globales, 1)
    ]
    fill_jg = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    _build_excel_data_sheet(
        ws_just_global,
        sheet_title="Justificaciones Acumuladas",
        tab_color="1E40AF",
        banner_title=f"{(system_config.nombre_institucion if system_config else 'QUIULACOCHA').upper()} - JUSTIFICACIONES ANUALES",
        banner_subtitle=f"Histórico consolidado anual | Total justificaciones: {len(justificadas_globales)}",
        headers=headers_jg,
        rows_data=rows_jg,
        header_fill=fill_jg,
    )

    # --- HOJA 6: DETALLE GENERAL COMPLETO ---
    ws_detail = wb.create_sheet("Detalle General")
    headers_dg = ['N°', 'EVENTO', 'FECHA', 'SOCIO', 'DNI', 'CONDICIÓN', 'ESTADO', 'INGRESO', 'SALIDA', 'OBSERVACIÓN']
    rows_dg = [
        [
            i,
            r['evento'],
            r['fecha'],
            r['socio'],
            r['dni'],
            r['condicion'],
            r['estado'],
            r['ingreso'],
            r['salida'],
            r['observacion'],
        ]
        for i, r in enumerate(detalle_global, 1)
    ]
    fill_dg = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    _build_excel_data_sheet(
        ws_detail,
        sheet_title="Detalle General",
        tab_color="334155",
        banner_title=f"{(system_config.nombre_institucion if system_config else 'QUIULACOCHA').upper()} - HISTORIAL COMPLETO ANUAL",
        banner_subtitle=f"Base de datos anual consolidada | Total registros: {len(detalle_global)}",
        headers=headers_dg,
        rows_data=rows_dg,
        header_fill=fill_dg,
        is_general=True,
    )

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="reporte_global_{datetime.now().year}.xlsx"'
    return response


def generate_user_attendance_excel(usuario, unified_list, stats, system_config=None):
    """
    Genera un archivo Excel Ejecutivo Individual para un comunero específico:
    - Encabezado con datos del socio
    - Tarjetas de KPIs (Total convocadas, asistencias, faltas, justificaciones, % asistencia)
    - Tabla detallada de todos los eventos históricos con autofiltros y estados
    """
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Historial Individual"
    ws.sheet_properties.tabColor = "0D9488"
    ws.views.sheetView[0].showGridLines = True

    font_inst = Font(name="Calibri", size=14, bold=True, color="0F172A")
    font_sub = Font(name="Calibri", size=10, bold=True, color="64748B")
    font_card_label = Font(name="Calibri", size=9, bold=True, color="64748B")
    font_card_val = Font(name="Calibri", size=10, bold=True, color="0F172A")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10, color="0F172A")
    font_correlativo = Font(name="Calibri", size=10, color="64748B")

    fill_header = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
    fill_card = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    fill_zebra_even = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    fill_zebra_odd = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

    border_soft = Border(
        left=Side(style='thin', color="E2E8F0"),
        right=Side(style='thin', color="E2E8F0"),
        top=Side(style='thin', color="E2E8F0"),
        bottom=Side(style='thin', color="E2E8F0")
    )
    border_thick_bottom = Border(
        left=Side(style='thin', color="CBD5E1"),
        right=Side(style='thin', color="CBD5E1"),
        top=Side(style='thin', color="CBD5E1"),
        bottom=Side(style='medium', color="0F172A")
    )

    badge_styles = {
        'FALTA': {
            'fill': PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="991B1B"),
        },
        'ASISTIÓ': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="166534"),
        },
        'ASISTIO': {
            'fill': PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="166534"),
        },
        'EXONERADO': {
            'fill': PatternFill(start_color="F3E8FF", end_color="F3E8FF", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="6B21A8"),
        },
        'JUSTIFICADA': {
            'fill': PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="1E40AF"),
        },
        'PENDIENTE': {
            'fill': PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid"),
            'font': Font(name="Calibri", size=10, bold=True, color="92400E"),
        },
    }

    inst_nombre = (system_config.nombre_institucion if system_config else "COMUNIDAD CAMPESINA DE QUIULACOCHA").upper()
    ws['B2'] = inst_nombre
    ws['B2'].font = font_inst
    ws['B3'] = f"EXPEDIENTE INDIVIDUAL DE ASISTENCIA Y FALTAS - {datetime.now().year}"
    ws['B3'].font = font_sub

    condicion_str = usuario.get_estado_display() if hasattr(usuario, 'get_estado_display') else str(getattr(usuario, 'estado', ''))
    score_str = f"{stats.get('score', 0):.1f}%"
    total_ev = stats.get('total_eventos', 0)
    faltas_count = stats.get('faltas', 0)

    if condicion_str.upper() == 'EXONERADO' or getattr(usuario, 'estado', None) == Usuario.ESTADO_EXONERADO:
        situacion = 'EXONERADO'
    elif faltas_count == 0:
        situacion = 'AL DÍA (0 FALTAS)'
    elif faltas_count <= 2:
        situacion = f'OBSERVADO ({faltas_count} FALTAS)'
    else:
        situacion = f'CON SANCIÓN ({faltas_count} FALTAS)'

    cards = [
        ("B5", "COMUNERO:", f"{usuario.apellido}, {usuario.nombre}"),
        ("B6", "DNI:", str(usuario.dni)),
        ("B7", "CONDICIÓN:", condicion_str),
        ("D5", "TOTAL CONVOCADAS:", total_ev),
        ("D6", "ASISTENCIAS:", stats.get('asistencias', 0)),
        ("D7", "FALTAS:", faltas_count),
        ("F5", "JUSTIFICADAS:", stats.get('justificadas', 0)),
        ("F6", "% ASISTENCIA:", score_str),
        ("F7", "SITUACIÓN:", situacion),
    ]

    for cell_pos, label, val in cards:
        col_letter = cell_pos[0]
        row_num = int(cell_pos[1])
        next_col_letter = chr(ord(col_letter) + 1)

        lbl_cell = ws[f"{col_letter}{row_num}"]
        lbl_cell.value = label
        lbl_cell.font = font_card_label
        lbl_cell.fill = fill_card
        lbl_cell.border = border_soft
        lbl_cell.alignment = Alignment(horizontal="right", vertical="center")

        val_cell = ws[f"{next_col_letter}{row_num}"]
        val_cell.value = val
        val_cell.font = font_card_val
        val_cell.border = border_soft
        val_cell.alignment = Alignment(horizontal="left", vertical="center")
        if label == "DNI:":
            val_cell.number_format = '@'

    headers = ['N°', 'EVENTO / ASAMBLEA', 'FECHA', 'UBICACIÓN', 'HORA INGRESO', 'HORA SALIDA', 'ESTADO', 'OBSERVACIÓN']
    ws.row_dimensions[9].height = 6
    ws.row_dimensions[10].height = 26
    for col_idx, h in enumerate(headers, 1):
        c = ws.cell(row=10, column=col_idx, value=h)
        c.fill = fill_header
        c.font = font_header
        c.border = border_thick_bottom
        c.alignment = Alignment(horizontal="center", vertical="center")

    curr_row = 11
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")

    for idx, item in enumerate(unified_list, 1):
        row_fill = fill_zebra_even if (idx % 2 == 0) else fill_zebra_odd
        ev = getattr(item, 'evento', None)
        ev_nombre = ev.nombre if ev else "Sin evento"

        fecha_val = getattr(item, 'fecha', None)
        fecha_str = fecha_val.strftime('%d/%m/%Y') if fecha_val else (ev.fecha.strftime('%d/%m/%Y') if ev and ev.fecha else "--")

        ubicacion_val = getattr(item, 'ubicacion', None)
        ubicacion_str = ubicacion_val.nombre if ubicacion_val else (getattr(ev, 'lugar', None) or getattr(ev, 'descripcion', None) or "Comunidad")

        is_absent = getattr(item, 'is_absent', False)
        es_justificada = getattr(item, 'es_justificada', False)
        is_late_absent = getattr(item, 'is_late_absent', False)

        if is_absent:
            status_text = "JUSTIFICADA" if es_justificada else "FALTA"
        elif getattr(usuario, 'estado', None) == Usuario.ESTADO_EXONERADO:
            status_text = "ASISTIÓ (EXONERADO)"
        elif not getattr(item, 'hora_salida', None):
            status_text = "PENDIENTE"
        else:
            status_text = "ASISTIÓ"

        if getattr(usuario, 'estado', None) == Usuario.ESTADO_EXONERADO and not is_absent:
            ingreso_str = "EXONERADO"
            salida_str = "EXONERADO"
        else:
            h_in = getattr(item, 'hora_ingreso', None)
            h_out = getattr(item, 'hora_salida', None)
            ingreso_str = h_in.strftime('%H:%M:%S') if h_in else "--"
            salida_str = h_out.strftime('%H:%M:%S') if h_out else "--"

        obs = getattr(item, 'justificacion_obs', "") or ""
        if is_late_absent:
            obs = "TARDE" if not obs else f"{obs} | TARDE"

        row_vals = [
            (idx, align_center, font_correlativo),
            (ev_nombre, align_left, font_data),
            (fecha_str, align_center, font_data),
            (ubicacion_str, align_left, font_data),
            (ingreso_str, align_center, font_data),
            (salida_str, align_center, font_data),
            (status_text, align_center, font_data),
            (obs, align_left, font_data),
        ]

        for col_idx, (val, alignment, font_style) in enumerate(row_vals, 1):
            c = ws.cell(row=curr_row, column=col_idx, value=val)
            c.border = border_soft
            c.fill = row_fill
            c.alignment = alignment
            c.font = font_style

            if col_idx == 7:
                for k, b in badge_styles.items():
                    if k in status_text:
                        c.fill = b['fill']
                        c.font = b['font']
                        break

        curr_row += 1

    if len(unified_list) == 0:
        c = ws.cell(row=curr_row, column=1, value="No se registran eventos o asistencias para este comunero.")
        c.font = Font(name="Calibri", size=10, italic=True, color="64748B")
        curr_row += 1

    ws.freeze_panes = "A11"

    if len(unified_list) > 0:
        ws.auto_filter.ref = f"A10:H{curr_row - 1}"

    ws.column_dimensions['A'].width = 6
    ws.column_dimensions['B'].width = 32
    ws.column_dimensions['C'].width = 14
    ws.column_dimensions['D'].width = 20
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 15
    ws.column_dimensions['G'].width = 18
    ws.column_dimensions['H'].width = 30

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"reporte_asistencia_{usuario.dni}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    response = HttpResponse(
        output.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
