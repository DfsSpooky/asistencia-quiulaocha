from django.db import models
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import logout as auth_logout, authenticate, login as auth_login
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.signing import BadSignature
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse, FileResponse, Http404
from django.views.decorators.cache import cache_page
from django.views import View
from django.db.models import Q, Count
from django.db import connection
from django.db import transaction
import csv
import openpyxl # Changed from `from openpyxl import Workbook` to `import openpyxl` for consistency with other imports
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from io import BytesIO
from .models import Usuario, Asistencia, Ubicacion, Evento, LogAccion, ConfiguracionSistema, Justificacion, HistorialCarnet, SolicitudDescargaCarnets
from .forms import FiltroAsistenciaForm, ImportarUsuariosForm, BuscarUsuarioForm, UsuarioRegistroForm, JustificacionForm, AdminJustificacionForm, CarnetForm, AdminCarnetForm
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import permission_classes, api_view
from django.utils import timezone
from django.utils.html import escape
from datetime import date, datetime, timedelta
import base64
from io import TextIOWrapper
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from urllib.parse import quote
from .utils.reports import (
    generate_attendance_csv, generate_attendance_excel, 
    generate_pdf_report, get_logo_base64, get_filtered_attendance_data
)
from .utils.attendance_rules import classify_attendance_item
from .qr_security import decode_qr_encoded_payload
import io
import json
import os
import subprocess
import shutil
import sys
import tarfile
import tempfile
import zipfile
from django.db.migrations.executor import MigrationExecutor
from .audit import log_critical_change
import logging


logger = logging.getLogger(__name__)


def _iter_queryset_in_chunks(queryset, chunk_size):
    total = queryset.count()
    for start in range(0, total, chunk_size):
        yield list(queryset[start:start + chunk_size])


def _read_image_field_base64(image_field, field_label):
    if not image_field or not getattr(image_field, 'name', ''):
        return None, f'Falta {field_label}.'

    try:
        storage = image_field.storage
        if not storage.exists(image_field.name):
            return None, f'Archivo de {field_label} no encontrado.'

        with storage.open(image_field.name, 'rb') as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8'), None
    except Exception:
        logger.exception(
            "No se pudo leer %s para el archivo %s",
            field_label,
            getattr(image_field, 'name', ''),
        )
        return None, f'Archivo de {field_label} inválido o inaccesible.'


def _crear_incidencia_carnet(usuario, motivo):
    return {
        'dni': usuario.dni,
        'apellido': usuario.apellido,
        'nombre': usuario.nombre,
        'estado_actual': usuario.get_estado_display(),
        'motivo': motivo,
    }


def _build_carnet_payload(usuario):
    foto_base64, foto_error = _read_image_field_base64(usuario.foto_perfil, 'fotografía')
    if foto_error:
        return None, _crear_incidencia_carnet(usuario, foto_error)

    qr_base64, qr_error = _read_image_field_base64(usuario.qr_code, 'código QR')
    if qr_error:
        return None, _crear_incidencia_carnet(usuario, qr_error)

    return {
        'nombre': usuario.nombre,
        'apellido': usuario.apellido,
        'dni': usuario.dni,
        'estado': usuario.get_estado_display(),
        'id': usuario.id,
        'foto_base64': foto_base64,
        'qr_base64': qr_base64,
    }, None


def _build_carnet_payload_sin_foto_permitida(usuario, foto_base64_override=None):
    qr_base64, qr_error = _read_image_field_base64(usuario.qr_code, 'código QR')
    if qr_error:
        return None, _crear_incidencia_carnet(usuario, qr_error)

    return {
        'nombre': usuario.nombre,
        'apellido': usuario.apellido,
        'dni': usuario.dni,
        'estado': usuario.get_estado_display(),
        'id': usuario.id,
        'foto_base64': foto_base64_override,
        'qr_base64': qr_base64,
    }, None


def _get_recent_carnet_download_requests(user, limit=5):
    return list(
        SolicitudDescargaCarnets.objects.filter(solicitado_por=user)
        .order_by('-fecha_solicitud')[:limit]
    )


def _get_active_carnet_download_request(user):
    return (
        SolicitudDescargaCarnets.objects.filter(
            solicitado_por=user,
            estado__in=[
                SolicitudDescargaCarnets.ESTADO_PENDIENTE,
                SolicitudDescargaCarnets.ESTADO_PROCESANDO,
            ],
        )
        .order_by('-fecha_solicitud')
        .first()
    )


def _cleanup_previous_carnet_requests(user, keep_request_id=None):
    previous_requests = SolicitudDescargaCarnets.objects.filter(solicitado_por=user)
    if keep_request_id is not None:
        previous_requests = previous_requests.exclude(pk=keep_request_id)

    for previous_request in previous_requests:
        if previous_request.archivo_zip:
            previous_request.archivo_zip.delete(save=False)
    previous_requests.delete()


def _launch_carnets_generation_job(solicitud_id):
    manage_py = os.path.join(settings.BASE_DIR, 'manage.py')
    command = [
        sys.executable,
        manage_py,
        'procesar_descarga_carnets',
        str(solicitud_id),
    ]
    with open(os.devnull, 'ab') as devnull:
        subprocess.Popen(
            command,
            cwd=settings.BASE_DIR,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
            close_fds=True,
        )


def _generate_carnets_zip_payload():
    import gc
    import time
    from .utils.reports import get_logo_base64
    started_perf = time.perf_counter()

    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError("WeasyPrint no está instalado.") from exc

    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError as exc:
        raise RuntimeError("pypdf no está instalado. Añade 'pypdf' a requirements.txt") from exc

    started_at = timezone.localtime()
    current_date = started_at.strftime('%d/%m/%Y %H:%M')
    timestamp = started_at.strftime('%Y%m%d_%H%M%S')
    zip_filename = f'carnets_y_pendientes_{timestamp}.zip'

    filtro_sin_foto = Q(foto_perfil__isnull=True) | Q(foto_perfil='')
    base_queryset = Usuario.objects.order_by('apellido', 'nombre').only(
        'id', 'nombre', 'apellido', 'dni', 'estado', 'foto_perfil', 'qr_code'
    )
    usuarios_con_foto_qs = base_queryset.exclude(filtro_sin_foto)
    usuarios_sin_foto_qs = base_queryset.filter(filtro_sin_foto)

    reporte_sin_foto = [
        _crear_incidencia_carnet(usuario, 'Falta fotografía.')
        for usuario in usuarios_sin_foto_qs
    ]
    incidencias = []

    logo = get_logo_base64()
    sys_config = ConfiguracionSistema.objects.first()
    avatar_carnet_sin_foto_base64 = None
    if sys_config and sys_config.avatar_carnet_sin_foto:
        avatar_carnet_sin_foto_base64, _ = _read_image_field_base64(
            sys_config.avatar_carnet_sin_foto,
            'avatar por defecto para carnet sin foto',
        )
    zip_buffer = io.BytesIO()
    zip_members = []
    total_carnets_con_foto = 0
    total_carnets_sin_foto = 0

    with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
        def generar_pdf_vacio(nombre_archivo, titulo, mensaje):
            html_vacio = f"""
            <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        @page {{ size: A4 landscape; margin: 18mm; }}
                        body {{ font-family: Helvetica, Arial, sans-serif; color: #1f2937; }}
                        h1 {{ color: #92400e; margin-bottom: 8px; }}
                        p {{ font-size: 12px; line-height: 1.5; }}
                    </style>
                </head>
                <body>
                    <h1>{titulo}</h1>
                    <p>{mensaje}</p>
                    <p>Generado el {current_date}.</p>
                </body>
            </html>
            """
            pdf_vacio = HTML(string=html_vacio).write_pdf()
            zip_file.writestr(nombre_archivo, pdf_vacio)
            zip_members.append(nombre_archivo)

        def generar_pdf_carnets(queryset, build_payload, zip_entry_name, titulo_vacio, mensaje_vacio, modo_sin_foto=False):
            total_carnets = 0
            total_queryset = queryset.count()
            if not total_queryset:
                generar_pdf_vacio(zip_entry_name, titulo_vacio, mensaje_vacio)
                return total_carnets

            chunk_size = 10
            writer = PdfWriter()

            for batch_number, chunk_users in enumerate(
                _iter_queryset_in_chunks(queryset, chunk_size),
                start=1,
            ):
                usuarios_data = []
                usuarios_validos_chunk = []
                for u in chunk_users:
                    carnet_payload, incidencia = build_payload(u)
                    if incidencia:
                        incidencias.append(incidencia)
                        continue
                    usuarios_data.append(carnet_payload)
                    usuarios_validos_chunk.append(u)

                if not usuarios_data:
                    gc.collect()
                    continue

                context = {
                    'usuarios': usuarios_data,
                    'logo_base64': logo,
                    'current_date': current_date,
                    'total_usuarios': len(usuarios_data),
                    'sistema_config': sys_config,
                    'modo_sin_foto': modo_sin_foto,
                }

                try:
                    html_string = render_to_string('asistencia/reporte_todos_carnets.html', context)
                    pdf_bytes = HTML(string=html_string).write_pdf()

                    reader = PdfReader(io.BytesIO(pdf_bytes))
                    for page in reader.pages:
                        writer.add_page(page)
                    total_carnets += len(usuarios_data)
                except Exception as exc:
                    logger.exception("Error al generar el lote %s de %s", batch_number, zip_entry_name)
                    for u in usuarios_validos_chunk:
                        incidencias.append(
                            _crear_incidencia_carnet(
                                u,
                                f'Error al generar carnet: {exc.__class__.__name__}.'
                            )
                        )
                    html_string = None
                    pdf_bytes = None
                    reader = None

                del pdf_bytes, reader, html_string, usuarios_data, usuarios_validos_chunk, context
                gc.collect()

                processed = min(batch_number * chunk_size, total_queryset)
                logger.info(
                    "Carnets progreso %s: %s/%s procesados",
                    zip_entry_name,
                    processed,
                    total_queryset,
                )

            if total_carnets:
                output_buffer = io.BytesIO()
                writer.write(output_buffer)
                final_pdf = output_buffer.getvalue()
                output_buffer.close()

                zip_file.writestr(zip_entry_name, final_pdf)
                zip_members.append(zip_entry_name)

            del writer
            gc.collect()
            return total_carnets

        total_carnets_con_foto = generar_pdf_carnets(
            usuarios_con_foto_qs,
            _build_carnet_payload,
            'carnets_con_foto.pdf',
            'Carnets con foto',
            'No se encontraron usuarios con fotografia disponible para generar carnets en este lote.',
        )
        total_carnets_sin_foto = generar_pdf_carnets(
            usuarios_sin_foto_qs,
            lambda usuario: _build_carnet_payload_sin_foto_permitida(
                usuario,
                foto_base64_override=avatar_carnet_sin_foto_base64,
            ),
            'carnets_sin_foto.pdf',
            'Carnets sin foto',
            'No se encontraron usuarios sin fotografia pendientes en este lote.',
            modo_sin_foto=True,
        )

        context_sin_foto = {
            'usuarios': reporte_sin_foto,
            'logo_base64': logo,
            'current_date': current_date,
            'sistema_config': sys_config,
        }
        html_sin_foto = render_to_string(
            'asistencia/reporte_sin_foto.html',
            context_sin_foto
        )
        pdf_sin_foto = HTML(string=html_sin_foto).write_pdf()
        zip_file.writestr('reporte_usuarios_sin_foto.pdf', pdf_sin_foto)
        zip_members.append('reporte_usuarios_sin_foto.pdf')

        del html_sin_foto, pdf_sin_foto, context_sin_foto
        gc.collect()

    zip_bytes = zip_buffer.getvalue()
    zip_buffer.close()
    duration_seconds = time.perf_counter() - started_perf

    return {
        'zip_bytes': zip_bytes,
        'zip_filename': zip_filename,
        'zip_members': zip_members,
        'total_carnets_con_foto': total_carnets_con_foto,
        'total_carnets_sin_foto': total_carnets_sin_foto,
        'total_usuarios_sin_foto': len(reporte_sin_foto),
        'total_incidencias': len(incidencias),
        'duration_seconds': duration_seconds,
    }

def landing_page(request):
    """
    Landing page pública para el sistema de asistencia de Quiulacocha.
    
    Si el usuario está autenticado, muestra un botón para ir al dashboard.
    Si no está autenticado, muestra la página de bienvenida con CTA para login.
    """
    config = ConfiguracionSistema.objects.first()
    return render(request, 'asistencia/landing.html', {
        'is_authenticated': request.user.is_authenticated,
        'config': config,
    })

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def dashboard(request):
    total_comunidad = Usuario.objects.count()
    padron_activo = Usuario.objects.filter(estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]).count()
    asistencias_hoy = Asistencia.objects.filter(fecha=date.today()).count()
    eventos_activos = Evento.objects.filter(activo=True).count()
    
    context = {
        'total_comunidad': total_comunidad,
        'padron_activo': padron_activo,
        'asistencias_hoy': asistencias_hoy,
        'eventos_activos': eventos_activos,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    }
    return render(request, 'asistencia/dashboard.html', context)


@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def panel_salud(request):
    db_status = 'OK'
    db_latency_ms = None
    try:
        start = timezone.now()
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        db_latency_ms = int((timezone.now() - start).total_seconds() * 1000)
    except Exception:
        db_status = 'ERROR'

    media_size = _dir_size(settings.MEDIA_ROOT)
    backups_dir = os.path.join(settings.BASE_DIR, 'backups')
    backups_size = _dir_size(backups_dir)

    latest_backup = None
    if os.path.exists(backups_dir):
        backup_files = [
            os.path.join(backups_dir, f)
            for f in os.listdir(backups_dir)
            if f.endswith('.tar.gz')
        ]
        if backup_files:
            latest_backup = max(backup_files, key=os.path.getmtime)

    disk_total, disk_used, disk_free = shutil.disk_usage(settings.BASE_DIR)

    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets)

    restore_log = LogAccion.objects.filter(accion__icontains='Restaurar Backup').order_by('-fecha').first()

    context = {
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr'),
        'health': {
            'db_status': db_status,
            'db_latency_ms': db_latency_ms,
            'db_engine': connection.vendor,
            'pending_migrations': len(plan),
            'disk_total': _format_bytes(disk_total),
            'disk_used': _format_bytes(disk_used),
            'disk_free': _format_bytes(disk_free),
            'media_size': _format_bytes(media_size),
            'backups_size': _format_bytes(backups_size),
            'latest_backup': os.path.basename(latest_backup) if latest_backup else 'No disponible',
            'latest_backup_at': datetime.fromtimestamp(os.path.getmtime(latest_backup)).strftime('%d/%m/%Y %H:%M') if latest_backup else 'N/A',
            'last_restore': restore_log.fecha.strftime('%d/%m/%Y %H:%M') if restore_log else 'Sin restauraciones',
        }
    }
    return render(request, 'asistencia/panel_salud.html', context)

class CustomLoginView(LoginView):
    template_name = 'asistencia/login.html'
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context['can_scan_qr'] = user.is_authenticated and user.has_perm('asistencia.can_scan_qr')
        return context

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated:
            if user.is_staff:
                return '/lista_usuarios/'
            else:
                return '/perfil/'
        return '/login/'

def custom_login(request):
    return CustomLoginView.as_view()(request)

@require_POST
def custom_logout(request):
    auth_logout(request)
    return redirect('login')

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def lista_usuarios(request):
    form = BuscarUsuarioForm(request.GET or None)
    usuarios = Usuario.objects.all().order_by('dni')
    
    if form.is_valid():
        query = form.cleaned_data.get('query')
        estado = form.cleaned_data.get('estado')
        ordenar_por = form.cleaned_data.get('ordenar_por')
        
        if query:
            usuarios = usuarios.filter(
                models.Q(nombre__icontains=query) |
                models.Q(apellido__icontains=query) |
                models.Q(dni__icontains=query)
            )
        
        if estado:
            usuarios = usuarios.filter(estado=estado)
        
        if ordenar_por:
            usuarios = usuarios.order_by(ordenar_por)
        else:
            usuarios = usuarios.order_by('dni')

    usuarios = usuarios.annotate(
        tiene_carnet_activo=models.Exists(
            HistorialCarnet.objects.filter(
                usuario=models.OuterRef('pk'),
                estado='Activo',
            )
        )
    )
    
    # Calcular estadísticas basadas en los resultados filtrados
    total_usuarios = usuarios.count()
    usuarios_activos = usuarios.filter(estado=Usuario.ESTADO_ACTIVO).count()
    usuarios_pasivos = usuarios.filter(estado=Usuario.ESTADO_PASIVO).count()
    usuarios_exonerados = usuarios.filter(estado=Usuario.ESTADO_EXONERADO).count()
    
    paginator = Paginator(usuarios, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'form': form,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr'),
        'total_usuarios': total_usuarios,
        'usuarios_activos': usuarios_activos,
        'usuarios_pasivos': usuarios_pasivos,
        'usuarios_exonerados': usuarios_exonerados,
        'solicitudes_descarga_carnets': _get_recent_carnet_download_requests(request.user),
        'solicitud_descarga_activa': _get_active_carnet_download_request(request.user),
    }
    if request.headers.get('HX-Request') and ('page' in request.GET or request.GET.get('query') or request.GET.get('estado') or request.GET.get('ordenar_por')):
        return render(request, 'asistencia/partials/user_list.html', context)

    return render(request, 'asistencia/lista_usuarios.html', context)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def detalle_usuario(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    context = {
        'usuario': usuario,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    }
    return render(request, 'asistencia/detalle_usuario.html', context)

@login_required
@permission_required('asistencia.can_scan_qr', raise_exception=True)
def escanear_qr(request, evento_id=None):
    # Obtener eventos para el selector
    eventos = Evento.objects.filter(activo=True).order_by('-fecha')
    
    # Si hay un evento seleccionado o predeterminado, obtener sus stats vivos
    stats = {'presentes': 0, 'total': Usuario.objects.filter(estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_PASIVO, Usuario.ESTADO_EXONERADO]).count()}
    target_event = None
    if eventos.exists():
        target_event = eventos.first()
        stats['presentes'] = Asistencia.objects.filter(evento=target_event).count()

    return render(request, 'asistencia/escanear.html', {
        'eventos': eventos,
        'ubicaciones': Ubicacion.objects.all(),
        'stats': stats,
        'selected_evento_id': target_event.id if target_event else None,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr'),
        'can_manage_users': request.user.has_perm('asistencia.can_manage_users'),
    })

@login_required
@permission_required('asistencia.can_scan_qr', raise_exception=True)
def keep_alive(request):
    """
    Vista para mantener viva la sesión del usuario mientras está en la página de escaneo.
    """
    return JsonResponse({'status': 'success', 'message': 'Sesión mantenida activa'})


def healthcheck(request):
    """
    Endpoint público y liviano para health checks de Docker/Hestia.
    No depende de sesión ni de datos del sistema.
    """
    return JsonResponse({'status': 'ok'})

def _format_bytes(size):
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024


def _dir_size(path):
    if not os.path.exists(path):
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for file_name in files:
            fp = os.path.join(root, file_name)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    return total


def _absolute_media_url(request, relative_or_absolute_url):
    if not relative_or_absolute_url:
        return None
    return request.build_absolute_uri(relative_or_absolute_url)


def _compute_unified_stats(unified_list, total_padron, tardanza_activa):
    for item in unified_list:
        flags = classify_attendance_item(item, tardanza_activa)
        item.is_late_absent = flags.is_late_absent
        item.is_absent = flags.is_absent

    asistencias_fisicas = sum(
        1 for item in unified_list if not getattr(item, 'is_absent', False) and (
            getattr(item, 'hora_salida', None) or item.usuario.estado == Usuario.ESTADO_EXONERADO
        )
    )
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    inasistencias_reales = sum(
        1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False)
    )
    pendientes = sum(
        1 for item in unified_list if not getattr(item, 'is_absent', False) and not getattr(item, 'hora_salida', None)
        and item.usuario.estado != Usuario.ESTADO_EXONERADO
    )
    total_tardanzas = sum(1 for item in unified_list if getattr(item, 'is_late_absent', False))
    total_asistentes_efectivos = asistencias_fisicas + justificadas
    porcentaje_asistencia = (total_asistentes_efectivos / total_padron * 100) if total_padron > 0 else 0

    return {
        'asistencias_fisicas': asistencias_fisicas,
        'justificadas': justificadas,
        'inasistencias_reales': inasistencias_reales,
        'pendientes': pendientes,
        'total_tardanzas': total_tardanzas,
        'total_asistentes_efectivos': total_asistentes_efectivos,
        'porcentaje_asistencia': porcentaje_asistencia,
    }


def _build_exonerado_consistency_alert(unified_list):
    total_exonerados_padron = Usuario.objects.filter(estado=Usuario.ESTADO_EXONERADO).count()
    exonerados_presentes = sum(
        1 for item in unified_list
        if getattr(getattr(item, 'usuario', None), 'estado', None) == Usuario.ESTADO_EXONERADO
        and not getattr(item, 'is_absent', False)
    )
    mismatch = total_exonerados_padron - exonerados_presentes
    return {
        'total_exonerados_padron': total_exonerados_padron,
        'exonerados_presentes': exonerados_presentes,
        'mismatch': mismatch,
        'has_mismatch': mismatch != 0,
    }


def _build_event_report_context(evento, filters=None):
    filters = filters or {}
    filters = {**filters, 'evento': evento}

    data = get_filtered_attendance_data(filters)
    unified_list = data.get('unified_report', [])
    sistema_config = ConfiguracionSistema.objects.first()
    tardanza_activa = bool(sistema_config and sistema_config.tardanza_activa)

    total_padron = Usuario.objects.filter(
        estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]
    ).count()

    stats = _compute_unified_stats(unified_list, total_padron, tardanza_activa)
    exonerado_alert = _build_exonerado_consistency_alert(unified_list)

    context = {
        'evento': evento,
        'unified_list': unified_list,
        'total_usuarios': total_padron,
        'total_asistentes': stats['total_asistentes_efectivos'],
        'justificadas': stats['justificadas'],
        'total_inasistentes': stats['inasistencias_reales'],
        'pendientes': stats['pendientes'],
        'total_tardanzas': stats['total_tardanzas'],
        'porcentaje_asistencia': stats['porcentaje_asistencia'],
        'exonerado_alert': exonerado_alert,
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64(),
        'sistema_config': sistema_config,
        'filtros': {
            'dni': filters.get('dni') or 'Todos',
            'fecha_inicio': filters.get('fecha_inicio').strftime('%d/%m/%Y') if filters.get('fecha_inicio') else None,
            'fecha_fin': filters.get('fecha_fin').strftime('%d/%m/%Y') if filters.get('fecha_fin') else None,
            'evento': evento.nombre
        }
    }
    return context, unified_list


@login_required
def solicitar_justificacion(request):
    try:
        usuario = Usuario.objects.get(user=request.user)
    except Usuario.DoesNotExist:
        return redirect('landing_page')

    if request.method == 'POST':
        form = JustificacionForm(request.POST, request.FILES)
        if form.is_valid():
            justificacion = form.save(commit=False)
            justificacion.usuario = usuario
            justificacion.save()
            return redirect('perfil_usuario')
    else:
        asistencias_ids = Asistencia.objects.filter(usuario=usuario).values_list('evento_id', flat=True)
        eventos_disponibles = Evento.objects.exclude(id__in=asistencias_ids)
        form = JustificacionForm()
        form.fields['evento'].queryset = eventos_disponibles
    
    return render(request, 'asistencia/solicitar_justificacion.html', {
        'form': form,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    })

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def admin_solicitar_justificacion(request):
    usuario_dni = request.GET.get('usuario_dni')
    initial_data = {}
    if usuario_dni:
        try:
            initial_data['usuario'] = Usuario.objects.get(dni=usuario_dni)
        except Usuario.DoesNotExist:
            pass

    if request.method == 'POST':
        form = AdminJustificacionForm(request.POST, request.FILES)
        if form.is_valid():
            justificacion = form.save(commit=False)
            before = {'estado': justificacion.estado}
            justificacion.estado = Justificacion.ESTADO_APROBADO
            justificacion.procesado_por = request.user
            justificacion.save()
            log_critical_change(
                request.user,
                f'Justificacion:{justificacion.id}',
                before,
                {'estado': justificacion.estado},
                action_label='Cambio estado justificacion',
            )
            return redirect('lista_usuarios')
    else:
        form = AdminJustificacionForm(initial=initial_data)
    
    # Contexto para el Centro de Control
    recent_justificaciones = Justificacion.objects.select_related('usuario', 'evento').order_by('-id')[:5]
    total_justificaciones = Justificacion.objects.filter(estado='APROBADO').count()
    
    return render(request, 'asistencia/admin_solicitar_justificacion.html', {
        'form': form,
        'recent_justificaciones': recent_justificaciones,
        'stats': {
            'total': total_justificaciones,
        },
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    })

class RegistrarAsistencia(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Validación de autenticación y permisos (capa HTTP)
        if not request.user.is_authenticated:
            return Response(
                {'error': 'No estás autenticado. Por favor, inicia sesión.'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        if not request.user.has_perm('asistencia.can_scan_qr'):
            return Response(
                {'error': 'No tienes permiso para registrar asistencias.'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Extraer datos del request
        encoded_dni = request.data.get('dni')
        ubicacion_id = request.data.get('ubicacion_id')
        evento_id = request.data.get('evento_id')
        tipo_escaneo = request.data.get('tipo_escaneo')
        
        # Extraer timestamp opcional (para sincronización offline)
        timestamp_str = request.data.get('timestamp')
        fecha_registro = None
        
        if timestamp_str:
            from django.utils.dateparse import parse_datetime
            fecha_registro = parse_datetime(timestamp_str)
            
            # Validar que el timestamp sea válido
            if not fecha_registro:
                return Response(
                    {'error': 'Formato de timestamp inválido. Use ISO 8601.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Validar tipo de escaneo
        if tipo_escaneo not in ['ingreso', 'salida']:
            return Response(
                {'error': 'Tipo de escaneo no válido. Debe ser "ingreso" o "salida".'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Verificamos firma y resolvemos el usuario por esquema actual (uid) o legado (dni).
            decoded_qr = decode_qr_encoded_payload(encoded_dni)
            if decoded_qr["kind"] == "uid_v1":
                usuario = Usuario.objects.get(qr_uid=decoded_qr["qr_uid"])
                if usuario.qr_version != decoded_qr["qr_version"]:
                    return Response(
                        {'error': 'Este código QR fue reemplazado por renovación/reposición. Solicite su carnet vigente.'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            else:
                usuario = Usuario.objects.get(dni=decoded_qr["dni"])
            
            # Obtener evento si fue especificado, o auto-vincular al evento activo de hoy
            evento = None
            if evento_id:
                evento = Evento.objects.get(id=evento_id, activo=True)
            elif not evento_id:
                fecha_efectiva = fecha_registro.date() if fecha_registro else date.today()
                evento = Evento.objects.filter(activo=True, fecha=fecha_efectiva).first()
            
            # Obtener ubicación si fue especificada
            ubicacion = None
            if ubicacion_id:
                ubicacion = Ubicacion.objects.get(id=ubicacion_id)
            
            # Delegar la lógica de negocio al servicio
            from .services import AsistenciaService
            
            if tipo_escaneo == 'ingreso':
                asistencia, message, hora = AsistenciaService.registrar_ingreso(
                    usuario=usuario,
                    evento=evento,
                    ubicacion=ubicacion,
                    request_user=request.user,
                    fecha_registro=fecha_registro
                )
            else:  # tipo_escaneo == 'salida'
                asistencia, message, hora = AsistenciaService.registrar_salida(
                    usuario=usuario,
                    evento=evento,
                    request_user=request.user,
                    fecha_registro=fecha_registro
                )
            
            # Retornar respuesta exitosa con metadatos extendidos para el Centro de Control
            return Response({
                'message': message,
                'hora': hora.strftime('%H:%M:%S') if hora else 'No registrado',
                'nombre': f"{usuario.nombre} {usuario.apellido}",
                'dni': usuario.dni,
                'estado': usuario.get_estado_display(),
                'is_exonerado': usuario.estado == Usuario.ESTADO_EXONERADO,
                'policy_note': (
                    'USUARIO EXONERADO: ADELANTE (sin penalización).'
                    if usuario.estado == Usuario.ESTADO_EXONERADO else ''
                ),
                'foto_perfil': _absolute_media_url(request, usuario.foto_perfil_url)
            }, status=status.HTTP_201_CREATED)
            
        except ValidationError as e:
            # Errores de validación de negocio
            return Response({'error': str(e.message)}, status=status.HTTP_400_BAD_REQUEST)
        
        except BadSignature:
            return Response(
                {'error': 'Alerta de Seguridad: C?digo QR falsificado o inv?lido.'},
                status=status.HTTP_403_FORBIDDEN
            )

        except Usuario.DoesNotExist:
            return Response(
                {'error': 'El DNI escaneado no corresponde a ningún usuario registrado.'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        except Evento.DoesNotExist:
            return Response(
                {'error': 'El evento seleccionado no es válido o no está activo.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        except Ubicacion.DoesNotExist:
            return Response(
                {'error': 'La ubicación seleccionada no es válida.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        except base64.binascii.Error:
            return Response(
                {'error': 'El código QR escaneado es inválido.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        except Exception:
            logger.exception("Error inesperado al registrar asistencia")
            return Response(
                {'error': 'Error interno del servidor.'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ListEventosActivos(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        if not request.user.has_perm('asistencia.can_scan_qr'):
            return Response({'error': 'Permiso denegado.'}, status=status.HTTP_403_FORBIDDEN)
        eventos = Evento.objects.filter(activo=True).order_by('-fecha')
        data = [{'id': e.id, 'nombre': e.nombre, 'fecha': e.fecha} for e in eventos]
        return Response(data)

class ListUbicaciones(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        if not request.user.has_perm('asistencia.can_scan_qr'):
            return Response({'error': 'Permiso denegado.'}, status=status.HTTP_403_FORBIDDEN)
        ubicaciones = Ubicacion.objects.all().order_by('nombre')
        data = [{'id': u.id, 'nombre': u.nombre} for u in ubicaciones]
        return Response(data)

class ListDentroEvento(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request, evento_id):
        if not request.user.has_perm('asistencia.can_scan_qr'):
            return Response({'error': 'Permiso denegado.'}, status=status.HTTP_403_FORBIDDEN)
        
        asistencias = Asistencia.objects.filter(
            evento_id=evento_id,
            fecha=date.today(),
            hora_salida__isnull=True
        ).select_related('usuario')
        
        data = [{
            'id': a.usuario.id,
            'dni': a.usuario.dni,
            'nombre': f"{a.usuario.nombre} {a.usuario.apellido}",
            'hora_ingreso': a.hora_ingreso.strftime('%H:%M:%S'),
            'foto_perfil': _absolute_media_url(request, a.usuario.foto_perfil_url)
        } for a in asistencias]
        return Response(data)

from rest_framework.permissions import AllowAny

class SystemConfigView(APIView):
    permission_classes = [AllowAny]
    def get(self, request):
        config = ConfiguracionSistema.objects.first()
        data = {
            'nombre_institucion': config.nombre_institucion if config else 'QUIULACOCHA',
            'logo_url': request.build_absolute_uri(config.logo.url) if config and config.logo else None
        }
        return Response(data)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def historial_asistencias(request):
    """
    Vista remodelada para centrarse en Evento y Estados (Asistieron/Faltaron).
    """
    form = FiltroAsistenciaForm(request.GET or None)
    filters = {}
    if form.is_valid():
        filters = form.cleaned_data
    
    # Asegurar que siempre haya un orden por defecto coherente
    if not filters.get('ordenar_por'):
        filters['ordenar_por'] = '-fecha'
        
    data = get_filtered_attendance_data(filters)
    # Usar siempre la lista unificada de la utilidad para mantener consistentes
    # faltas, justificadas y asistencias en tabla/contadores/reportes.
    unified_list = list(data.get('unified_report', []))
    
    # Re-ordenar la lista unificada si es necesario (ya que mezclamos tipos de objetos)
    # Por defecto, los asistentes van primero o según la lógica de reports.py
    
    total_registros = len(unified_list)
    # Mutuamente excluyentes:
    confirmadas = sum(
        1 for item in unified_list 
        if not getattr(item, 'is_absent', False) and 
        (getattr(item, 'hora_salida', None) or getattr(item, 'confirmada', False) or item.usuario.estado == 'EXONERADO')
    )
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    inasistencias = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    pendientes = sum(
        1 for item in unified_list 
        if not getattr(item, 'is_absent', False) and 
        not getattr(item, 'hora_salida', None) and 
        not getattr(item, 'confirmada', False) and 
        item.usuario.estado != 'EXONERADO'
    )
    
    # Lógica de Contadores según selección de evento
    advanced_filters_active = any(
        filters.get(key) for key in ['fecha_inicio', 'fecha_fin', 'ubicacion', 'confirmada']
    )
    if not filters.get('evento') and not advanced_filters_active and not filters.get('dni') and not filters.get('estado'):
        # Si no hay evento seleccionado, mostrar todo en CERO
        total_registros = 0
        confirmadas = 0
        justificadas = 0
        inasistencias = 0
        pendientes = 0
    else:
        # Si hay evento, "Padrón Esperado" debe ser el total de usuarios empadronados (Activos + Pasivos + Exonerados)
        # independientemente de cuántos registros traiga el filtrado (unified_list)
        if filters.get('evento'):
            total_registros = Usuario.objects.filter(
                estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]
            ).count()
        else:
            total_registros = len(unified_list)
    
    paginator = Paginator(unified_list, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    report_years = list(
        Evento.objects.order_by('-fecha')
        .values_list('fecha__year', flat=True)
        .distinct()
    )

    total_pendientes_global = Asistencia.objects.filter(
        hora_ingreso__isnull=False,
        hora_salida__isnull=True,
        confirmada=False,
        evento__isnull=False,
        es_justificada=False,
    ).exclude(usuario__estado=Usuario.ESTADO_EXONERADO).count()

    context = {
        'page_obj': page_obj,
        'form': form,
        'report_years': report_years,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr'),
        'total_pendientes_global': total_pendientes_global,
        'stats': {
            'total': total_registros,
            'confirmadas': confirmadas,
            'justificadas': justificadas,
            'inasistencias': inasistencias,
            'pendientes': pendientes,
        },
    }
    
    # Only return partial template for HTMX pagination/filter requests, not initial load
    # Check if ANY filter or pagination parameter is present, or if it's explicitly an HTMX request that expects the table
    if request.headers.get('HX-Request') and (
        'page' in request.GET or
        any(request.GET.get(f) for f in ['dni', 'evento', 'estado', 'fecha_inicio', 'fecha_fin', 'ubicacion', 'ordenar_por', 'confirmada'])
    ):
        return render(request, 'asistencia/historial_table.html', context)
        
    return render(request, 'asistencia/historial_asistencias.html', context)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_global_pdf(request):
    """
    Genera un PDF histórico de TODO el sistema, agrupado por eventos
    en orden cronológico (Enero a Diciembre).
    """
    from .utils.reports import get_logo_base64
    
    sistema_config = ConfiguracionSistema.objects.first()
    tardanza_activa = bool(sistema_config and sistema_config.tardanza_activa)

    selected_year_raw = request.GET.get('year')
    try:
        selected_year = int(selected_year_raw) if selected_year_raw else timezone.localdate().year
    except (TypeError, ValueError):
        return HttpResponse("El parámetro 'year' no es válido.", status=400)

    # Obtener eventos del año seleccionado ordenados por fecha ascendente (enero a diciembre)
    eventos = Evento.objects.filter(fecha__year=selected_year).order_by('fecha')
    if not eventos.exists():
        return HttpResponse(
            f"No hay eventos registrados para el año {selected_year}.",
            status=404
        )
    
    report_data = []
    total_general_asistencias = 0
    
    for ev in eventos:
        # Para cada evento, obtenemos sus datos usando la utilidad
        data = get_filtered_attendance_data({'evento': ev})
        asistencias = list(data.get('asistencias', []))
        no_asistentes = data.get('usuarios_no_asistentes', [])
        
        # Procesar récords unificados para este evento
        records = []
        seen_user_ids = set()
        for a in asistencias:
            if a.usuario_id in seen_user_ids:
                continue
            seen_user_ids.add(a.usuario_id)
            is_late_absent = bool(
                tardanza_activa
                and getattr(a, 'puntualidad', None) == Asistencia.PUNTUALIDAD_TARDE
                and a.usuario.estado != Usuario.ESTADO_EXONERADO
            )
            records.append({
                'usuario': a.usuario,
                'hora_ingreso': a.hora_ingreso,
                'hora_salida': a.hora_salida,
                'is_absent': a.es_justificada or is_late_absent,
                'is_late_absent': is_late_absent,
                'es_justificada': a.es_justificada,
            })
            if a.confirmada and not a.es_justificada and not is_late_absent:
                total_general_asistencias += 1
                
        for u in no_asistentes:
            if u.id in seen_user_ids:
                continue
            seen_user_ids.add(u.id)
            records.append({
                'usuario': u,
                'is_absent': True,
                'es_justificada': getattr(u, 'es_justificada', False),
                'justificacion_obs': getattr(u, 'justificacion_obs', '') or ''
            })
            
        # Estadísticas del evento
        total_padron = len(records)
        asistencias_fisicas = sum(1 for r in records if not r['is_absent'] and not r.get('es_justificada', False))
        justificadas = sum(1 for r in records if r.get('es_justificada', False))
        faltas_reales = sum(1 for r in records if r['is_absent'] and not r.get('es_justificada', False))
        presentes_totales = asistencias_fisicas + justificadas
        porcentaje = (presentes_totales / total_padron * 100) if total_padron > 0 else 0
        
        report_data.append({
            'evento': ev,
            'records': records,
            'stats': {
                'asistencias_fisicas': asistencias_fisicas,
                'justificadas': justificadas,
                'inasistencias': faltas_reales,
                'porcentaje': porcentaje,
                'total_usuarios': total_padron
            }
        })
        
    context = {
        'report_data': report_data,
        'logo_base64': get_logo_base64(),
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'sistema_config': sistema_config,
        'selected_year': selected_year,
        'total_eventos': len(eventos),
    }
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar Reporte Global PDF",
        descripcion=f"{request.user.username} generó el reporte anual consolidado {selected_year} de {len(eventos)} eventos."
    )
    
    return generate_pdf_report('asistencia/reporte_global_anual.html', context, f"reporte_global_{selected_year}.pdf")

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_backup(request):
    """
    Genera y descarga un backup completo (DB + Media).
    Solo para superusuarios por seguridad.
    """
    if not request.user.is_superuser:
        messages.error(request, "Solo los superusuarios pueden generar copias de seguridad.")
        return redirect('dashboard')

    try:
        from asistencia.management.commands.backup_db import Command as BackupCommand
        cmd = BackupCommand()
        file_path = cmd.handle()

        if file_path and os.path.exists(file_path):
            # Stream para evitar cargar un backup completo en memoria RAM.
            response = FileResponse(open(file_path, 'rb'), content_type="application/gzip")
            response['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'

            LogAccion.objects.create(
                usuario=request.user,
                accion="Generar Backup",
                descripcion=f"{request.user.username} generó y descargó una copia de seguridad."
            )
            return response
        else:
            messages.error(request, "Error al generar el archivo de backup.")

    except Exception as e:
        messages.error(request, f"Error inesperado al generar backup: {str(e)}")

    return redirect('dashboard')

def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
    base_path = os.path.abspath(path)
    for member in tar.getmembers():
        if os.path.isabs(member.name):
            raise Exception("Ruta absoluta detectada en el archivo de backup.")
        if member.issym() or member.islnk():
            raise Exception("No se permiten enlaces simbolicos o duros en backups.")
        member_path = os.path.join(path, member.name)
        if os.path.commonpath([os.path.abspath(member_path), base_path]) != base_path:
            raise Exception("Intento de Path Traversal detectado en el archivo de backup.")
    tar.extractall(path, members, numeric_owner=numeric_owner)


def validate_tar_limits(tar, *, max_members, max_total_uncompressed_size, label):
    members = tar.getmembers()
    if len(members) > max_members:
        raise Exception(f"{label}: demasiados archivos en el backup (limite {max_members}).")

    total_uncompressed = 0
    for member in members:
        if member.size < 0:
            raise Exception(f"{label}: archivo invalido detectado en el backup.")
        total_uncompressed += member.size
        if total_uncompressed > max_total_uncompressed_size:
            max_mb = max_total_uncompressed_size // (1024 * 1024)
            raise Exception(f"{label}: tamano descomprimido excede el limite de {max_mb} MB.")

def _import_postgres_sql_into_sqlite(sql_file, sqlite_path):
    import sqlite3
    from django.core.management import call_command

    connection.close()
    call_command('migrate', interactive=False)

    tables = {}
    current_table = None

    with open(sql_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line_clean = line.rstrip('\r\n')
            if line_clean.startswith('COPY public.'):
                table_name = line_clean.split()[1].replace('public.', '')
                col_str = line_clean[line_clean.find('(') + 1 : line_clean.find(')')]
                cols = [c.strip().strip('"') for c in col_str.split(',')]
                current_table = table_name
                tables[current_table] = {'cols': cols, 'rows': []}
            elif line_clean == r'\.' and current_table:
                current_table = None
            elif current_table:
                raw_vals = line_clean.split('\t')
                vals = [None if v == r'\N' else (1 if v == 't' else (0 if v == 'f' else v)) for v in raw_vals]
                tables[current_table]['rows'].append(vals)

    conn = sqlite3.connect(sqlite_path)
    cur = conn.cursor()
    cur.execute('PRAGMA foreign_keys = OFF;')

    for table_name, data in tables.items():
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        if not cur.fetchone():
            continue

        cur.execute(f'PRAGMA table_info("{table_name}");')
        cols_info = cur.fetchall()
        sqlite_cols = {row[1] for row in cols_info}

        target_indices = [idx for idx, c in enumerate(data['cols']) if c in sqlite_cols]
        target_cols = [data['cols'][idx] for idx in target_indices]

        if not target_cols:
            continue

        # Suplir columnas NOT NULL que no vinieron en el respaldo (ej. migraciones posteriores)
        missing_defaults = []
        for col_info in cols_info:
            cid, cname, ctype, notnull, dflt, pk = col_info
            if notnull and not pk and cname not in target_cols and dflt is None:
                ctype_lower = ctype.lower()
                if 'bool' in ctype_lower:
                    def_val = 1
                elif 'int' in ctype_lower or 'real' in ctype_lower or 'numeric' in ctype_lower:
                    def_val = 0
                else:
                    def_val = ''
                target_cols.append(cname)
                missing_defaults.append(def_val)

        cur.execute(f'DELETE FROM "{table_name}";')

        placeholders = ', '.join(['?'] * len(target_cols))
        col_names = ', '.join([f'"{c}"' for c in target_cols])
        insert_sql = f'INSERT OR REPLACE INTO "{table_name}" ({col_names}) VALUES ({placeholders})'

        batch = []
        for r in data['rows']:
            row_vals = []
            for idx in target_indices:
                val = r[idx]
                col_name = data['cols'][idx]
                if isinstance(val, str) and (col_name.endswith('_uid') or (len(val) == 36 and val.count('-') == 4)):
                    try:
                        from uuid import UUID
                        val = UUID(val).hex
                    except Exception:
                        pass
                row_vals.append(val)
            row_vals.extend(missing_defaults)
            batch.append(row_vals)
            if len(batch) >= 500:
                cur.executemany(insert_sql, batch)
                batch = []
        if batch:
            cur.executemany(insert_sql, batch)

    cur.execute('PRAGMA foreign_keys = ON;')
    conn.commit()
    conn.close()


@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def restaurar_backup(request):
    """
    Restaura el sistema (DB + Media) desde un archivo subido.
    Solo superusuarios.
    """
    is_ajax = (
        request.headers.get('x-requested-with') == 'XMLHttpRequest' or
        'application/json' in request.headers.get('Accept', '')
    )

    if not request.user.is_superuser:
        if is_ajax:
            return JsonResponse({'ok': False, 'error': "Solo superusuarios pueden restaurar backups."}, status=403)
        messages.error(request, "Solo superusuarios pueden restaurar backups.")
        return redirect('dashboard')

    if request.method == 'POST' and request.FILES.get('backup_file'):
        backup_file = request.FILES['backup_file']
        max_upload_size_bytes = 512 * 1024 * 1024  # 512 MB
        max_members = 25000
        max_uncompressed_main_bytes = 2 * 1024 * 1024 * 1024  # 2 GB
        max_uncompressed_media_bytes = 4 * 1024 * 1024 * 1024  # 4 GB

        current_user = request.user
        current_username = current_user.username
        current_email = getattr(current_user, 'email', '')
        current_password_hash = current_user.password

        before = {
            'usuarios': Usuario.objects.count(),
            'asistencias': Asistencia.objects.count(),
            'eventos': Evento.objects.count(),
        }

        if not backup_file.name.endswith('.tar.gz'):
            err = "El archivo debe ser un .tar.gz valido."
            if is_ajax:
                return JsonResponse({'ok': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('dashboard')

        if backup_file.size > max_upload_size_bytes:
            max_mb = max_upload_size_bytes // (1024 * 1024)
            err = f"El backup supera el limite de {max_mb} MB."
            if is_ajax:
                return JsonResponse({'ok': False, 'error': err}, status=400)
            messages.error(request, err)
            return redirect('dashboard')

        # Directorio temporal aislado para la restauracion
        temp_restore_root = tempfile.mkdtemp(prefix='restore_web_')
        archive_path = os.path.join(temp_restore_root, 'uploaded_backup.tar.gz')

        # Guardar archivo subido
        with open(archive_path, 'wb+') as f:
            for chunk in backup_file.chunks():
                f.write(chunk)

        try:
            # 1. Extraer el tar.gz principal
            with tarfile.open(archive_path, "r:gz") as tar:
                validate_tar_limits(
                    tar,
                    max_members=max_members,
                    max_total_uncompressed_size=max_uncompressed_main_bytes,
                    label="Backup principal",
                )
                safe_extract(tar, path=temp_restore_root)

            # Buscar dónde se encuentran database.sql, database.sqlite3 y media.tar.gz
            sql_file = None
            sqlite_file = None
            media_tar = None

            for root_dir, dirs, files in os.walk(temp_restore_root):
                if '__MACOSX' in root_dir:
                    continue
                if 'database.sql' in files and not sql_file:
                    sql_file = os.path.join(root_dir, 'database.sql')
                if 'database.sqlite3' in files and not sqlite_file:
                    sqlite_file = os.path.join(root_dir, 'database.sqlite3')
                if 'media.tar.gz' in files and not media_tar:
                    media_tar = os.path.join(root_dir, 'media.tar.gz')

            if not sql_file and not sqlite_file:
                raise Exception("Estructura de backup invalida: no se encontro database.sql ni database.sqlite3 en el archivo.")

            # 2. Restaurar Base de Datos
            db_conf = settings.DATABASES['default']
            db_engine = db_conf.get('ENGINE', '')

            if db_engine.endswith('sqlite3'):
                if sqlite_file and os.path.exists(sqlite_file):
                    connection.close()
                    shutil.copy2(sqlite_file, str(db_conf['NAME']))
                elif sql_file and os.path.exists(sql_file):
                    _import_postgres_sql_into_sqlite(sql_file, str(db_conf['NAME']))
                else:
                    raise Exception("No se encontro un archivo de base de datos compatible en el backup.")
            else:
                if not sql_file or not os.path.exists(sql_file):
                    raise Exception("No se encontro database.sql en el backup para PostgreSQL.")

                # Sanitizar database.sql (remover comandos de pg_dump 17 incompatibles con PostgreSQL 15: \restrict, \unrestrict y transaction_timeout)
                # y reiniciar el esquema public para asegurar una importación limpia sin tablas huérfanas
                with open(sql_file, 'r', encoding='utf-8', errors='ignore') as sf:
                    sql_lines = sf.readlines()
                sanitized_lines = [
                    "DROP SCHEMA IF EXISTS public CASCADE;\n",
                    "CREATE SCHEMA public;\n",
                    "GRANT ALL ON SCHEMA public TO CURRENT_USER;\n",
                ]
                for line in sql_lines:
                    strip_line = line.strip()
                    if (
                        strip_line.startswith(r'\restrict') or
                        strip_line.startswith(r'\unrestrict') or
                        strip_line.startswith('SET transaction_timeout')
                    ):
                        sanitized_lines.append(f'-- {line}')
                    else:
                        sanitized_lines.append(line)
                with open(sql_file, 'w', encoding='utf-8') as sf:
                    sf.writelines(sanitized_lines)

                connection.close()
                env = os.environ.copy()
                env['PGPASSWORD'] = str(db_conf.get('PASSWORD', ''))

                psql_cmd = ['psql']
                if db_conf.get('HOST'):
                    psql_cmd.extend(['-h', str(db_conf['HOST'])])
                if db_conf.get('PORT'):
                    psql_cmd.extend(['-p', str(db_conf['PORT'])])
                if db_conf.get('USER'):
                    psql_cmd.extend(['-U', str(db_conf['USER'])])
                psql_cmd.extend([
                    '-d', str(db_conf['NAME']),
                    '-v', 'ON_ERROR_STOP=1',
                    '--single-transaction',
                    '-f', sql_file
                ])

                res = subprocess.run(psql_cmd, env=env, capture_output=True, text=True)
                if res.returncode != 0:
                    err_detail = (res.stderr or res.stdout or '').strip()
                    raise Exception(f"Fallo en PostgreSQL (código {res.returncode}): {err_detail}")

            # Aplicar migraciones automáticas sobre la base recién restaurada
            # para asegurar compatibilidad si el backup venía de una versión anterior
            connection.close()
            from django.core.management import call_command
            call_command('migrate', interactive=False)

            # 3. Restaurar Media
            if media_tar and os.path.exists(media_tar):
                temp_media_dir = os.path.join(temp_restore_root, 'temp_media_extract')
                os.makedirs(temp_media_dir, exist_ok=True)

                with tarfile.open(media_tar, "r:gz") as mt:
                    validate_tar_limits(
                        mt,
                        max_members=max_members,
                        max_total_uncompressed_size=max_uncompressed_media_bytes,
                        label="Media backup",
                    )
                    safe_extract(mt, path=temp_media_dir)

                # Identificar carpeta origen (si vino como media/ o contenido directo)
                nested_media = os.path.join(temp_media_dir, 'media')
                source_media = nested_media if os.path.isdir(nested_media) else temp_media_dir

                media_root = settings.MEDIA_ROOT
                os.makedirs(media_root, exist_ok=True)

                for item in os.listdir(source_media):
                    if item == '__MACOSX':
                        continue
                    src_item = os.path.join(source_media, item)
                    dst_item = os.path.join(media_root, item)
                    if os.path.isdir(src_item):
                        if os.path.exists(dst_item):
                            shutil.rmtree(dst_item)
                        shutil.copytree(src_item, dst_item)
                    else:
                        shutil.copy2(src_item, dst_item)

            after = {
                'usuarios': Usuario.objects.count(),
                'asistencias': Asistencia.objects.count(),
                'eventos': Evento.objects.count(),
            }

            # Métricas detalladas de los archivos multimedia y tipos de reunión restaurados
            media_root = settings.MEDIA_ROOT
            perfil_fotos_dir = os.path.join(media_root, 'perfil_fotos')
            qr_codes_dir = os.path.join(media_root, 'qr_codes')
            logos_dir = os.path.join(media_root, 'logos')

            fotos_count = len(os.listdir(perfil_fotos_dir)) if os.path.exists(perfil_fotos_dir) else 0
            qr_count = len(os.listdir(qr_codes_dir)) if os.path.exists(qr_codes_dir) else 0
            logos_count = len(os.listdir(logos_dir)) if os.path.exists(logos_dir) else 0

            media_bytes = _dir_size(media_root) if os.path.exists(media_root) else 0
            media_size_str = _format_bytes(media_bytes)

            from asistencia.models import TipoEvento
            tipos_count = TipoEvento.objects.count()
            faenas_count = Evento.objects.filter(tipo_evento__categoria='FAENA').count()
            asambleas_count = Evento.objects.filter(tipo_evento__categoria='ASAMBLEA').count()

            msg = (
                f"Sistema restaurado exitosamente: {after['usuarios']} comuneros, "
                f"{after['asistencias']} asistencias, {after['eventos']} reuniones "
                f"({asambleas_count} asambleas, {faenas_count} faenas), "
                f"{fotos_count} fotos de perfil y {qr_count} códigos QR sincronizados."
            )
            # Preservar al superusuario en la base de datos restaurada y mantener su sesión activa
            try:
                from django.contrib.auth.models import User
                from django.contrib.auth import login as auth_login

                restored_user = User.objects.filter(username=current_username).first()
                if not restored_user:
                    restored_user = User.objects.create(
                        username=current_username,
                        email=current_email,
                        password=current_password_hash,
                        is_superuser=True,
                        is_staff=True,
                        is_active=True,
                    )
                else:
                    restored_user.password = current_password_hash
                    restored_user.is_superuser = True
                    restored_user.is_staff = True
                    restored_user.save(update_fields=['password', 'is_superuser', 'is_staff'])

                auth_login(request, restored_user, backend='django.contrib.auth.backends.ModelBackend')
                request.user = restored_user
                request.session.cycle_key()
            except Exception as session_exc:
                logger.warning("No se pudo regenerar la sesión post-restauración: %s", session_exc)
                try:
                    request.session.create()
                except Exception:
                    request.session.modified = False

            log_user = getattr(request, 'user', None)
            if not log_user or not getattr(log_user, 'is_authenticated', False):
                from django.contrib.auth.models import User
                log_user = User.objects.filter(is_superuser=True).first()

            if log_user:
                LogAccion.objects.create(
                    usuario=log_user,
                    accion="Restaurar Backup",
                    descripcion=(
                        f"{getattr(log_user, 'username', 'admin')} restauró el sistema desde {backup_file.name}. "
                        f"Usuarios: {after['usuarios']}, Asistencias: {after['asistencias']}."
                    )
                )
                log_critical_change(
                    log_user,
                    'Sistema',
                    before,
                    after,
                    action_label='Restauracion de backup',
                )

            if is_ajax:
                return JsonResponse({
                    'ok': True,
                    'message': msg,
                    'usuarios': after['usuarios'],
                    'asistencias': after['asistencias'],
                    'eventos': after['eventos'],
                    'fotos': fotos_count,
                    'qr_codes': qr_count,
                    'logos': logos_count,
                    'media_size': media_size_str,
                    'faenas': faenas_count,
                    'asambleas': asambleas_count,
                    'tipos_evento': tipos_count,
                })

        except Exception as e:
            err_msg = f"Error en restauracion: {str(e)}"
            if is_ajax:
                return JsonResponse({'ok': False, 'error': err_msg}, status=400)
            messages.error(request, err_msg)
        finally:
            if temp_restore_root and os.path.exists(temp_restore_root):
                try:
                    shutil.rmtree(temp_restore_root, ignore_errors=True)
                except Exception as clean_err:
                    logger.warning("No se pudo limpiar directorio temporal %s: %s", temp_restore_root, clean_err)

    elif request.method == 'POST' and not request.FILES.get('backup_file'):
        err = "Por favor selecciona un archivo .tar.gz para restaurar."
        if is_ajax:
            return JsonResponse({'ok': False, 'error': err}, status=400)
        messages.error(request, err)

    return redirect('dashboard')

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def exportar_asistencias_csv(request):
    form = FiltroAsistenciaForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    data = get_filtered_attendance_data(filters)
    unified_list = data.get('unified_report', [])
    return generate_attendance_csv(unified_list)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def exportar_asistencias_excel(request):
    form = FiltroAsistenciaForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    data = get_filtered_attendance_data(filters)
    unified_list = data.get('unified_report', [])
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Exportar Excel",
        descripcion=f"{request.user.username} exportó las asistencias a Excel."
    )
    return generate_attendance_excel(unified_list)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def exportar_reporte_global_excel(request):
    """
    Genera un Excel histórico de TODO el sistema, similar al reporte global PDF.
    """
    from .utils.reports import generate_global_attendance_excel, get_filtered_attendance_data
    
    system_config = ConfiguracionSistema.objects.first()
    tardanza_activa = bool(system_config and system_config.tardanza_activa)

    year_raw = request.GET.get('year')
    categoria_raw = request.GET.get('categoria')
    tipo_evento_raw = request.GET.get('tipo_evento')
    eventos = list(Evento.objects.all().order_by('fecha'))
    if year_raw:
        try:
            year_val = int(year_raw)
            eventos = [ev for ev in eventos if ev.fecha.year == year_val]
        except (ValueError, TypeError):
            pass

    if tipo_evento_raw:
        eventos = [
            ev for ev in eventos 
            if str(getattr(ev, 'tipo_evento_id', '')) == str(tipo_evento_raw) or ev.tipo_nombre == str(tipo_evento_raw)
        ]

    if categoria_raw:
        cat_upper = str(categoria_raw).upper()
        if cat_upper == 'FAENA':
            eventos = [ev for ev in eventos if ev.es_faena]
        elif cat_upper == 'ASAMBLEA':
            eventos = [ev for ev in eventos if ev.es_asamblea]

    report_data = []
    
    for ev in eventos:
        data = get_filtered_attendance_data({'evento': ev})
        asistencias = list(data.get('asistencias', []))
        no_asistentes = data.get('usuarios_no_asistentes', [])
        
        records = []
        seen_user_ids = set()
        for a in asistencias:
            if a.usuario_id in seen_user_ids:
                continue
            seen_user_ids.add(a.usuario_id)
            is_late_absent = bool(
                tardanza_activa
                and getattr(a, 'puntualidad', None) == Asistencia.PUNTUALIDAD_TARDE
                and a.usuario.estado != Usuario.ESTADO_EXONERADO
            )
            records.append({
                'usuario': a.usuario,
                'hora_ingreso': a.hora_ingreso,
                'hora_salida': a.hora_salida,
                'is_absent': a.es_justificada or is_late_absent,
                'is_late_absent': is_late_absent,
                'es_justificada': a.es_justificada,
            })
                
        for u in no_asistentes:
            if u.id in seen_user_ids:
                continue
            seen_user_ids.add(u.id)
            records.append({
                'usuario': u,
                'is_absent': True,
                'es_justificada': getattr(u, 'es_justificada', False),
                'justificacion_obs': getattr(u, 'justificacion_obs', '') or ''
            })
            
        total_padron = len(records)
        asistencias_fisicas = sum(1 for r in records if not r['is_absent'] and not r.get('es_justificada', False))
        justificadas = sum(1 for r in records if r.get('es_justificada', False))
        faltas_reales = sum(1 for r in records if r['is_absent'] and not r.get('es_justificada', False))
        presentes_totales = asistencias_fisicas + justificadas
        porcentaje = (presentes_totales / total_padron * 100) if total_padron > 0 else 0
        
        report_data.append({
            'evento': ev,
            'records': records,
            'stats': {
                'asistencias_fisicas': asistencias_fisicas,
                'justificadas': justificadas,
                'inasistencias': faltas_reales,
                'total_usuarios': total_padron,
                'porcentaje': porcentaje,
            }
        })
        
    LogAccion.objects.create(
        usuario=request.user,
        accion="Exportar Reporte Global Excel",
        descripcion=f"{request.user.username} exportó el reporte anual consolidado a Excel ({len(eventos)} eventos)."
    )
    
    return generate_global_attendance_excel(report_data, system_config)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def confirmar_asistencia(request, asistencia_id):
    asistencia = get_object_or_404(Asistencia, id=asistencia_id)
    if not asistencia.confirmada:
        asistencia.confirmada = True
        if not asistencia.hora_salida:
            asistencia.hora_salida = timezone.localtime(timezone.now()).time()
        asistencia.save()
        messages.success(request, f"Asistencia de {asistencia.usuario} confirmada exitosamente.")
        LogAccion.objects.create(
            usuario=request.user,
            accion="Confirmar asistencia",
            descripcion=f"{request.user.username} confirmó la asistencia de {asistencia.usuario.nombre} {asistencia.usuario.apellido} para el evento {asistencia.evento.nombre if asistencia.evento else 'sin evento'}."
            )
    else:
        messages.warning(request, f"La asistencia de {asistencia.usuario} ya estaba confirmada.")
    return redirect('historial_asistencias')


@login_required
def api_eventos_con_pendientes(request):
    """
    Retorna la lista de eventos que tienen comuneros pendientes de salida,
    junto con el contador de pendientes por cada evento y el total global.
    """
    if not (request.user.is_superuser or request.user.is_staff or request.user.has_perm('asistencia.can_manage_users')):
        return JsonResponse({'error': 'No tiene permisos para acceder a la regularización.'}, status=403)

    pendientes_filter = Q(
        asistencia__hora_ingreso__isnull=False,
        asistencia__hora_salida__isnull=True,
        asistencia__confirmada=False,
        asistencia__es_justificada=False,
    ) & ~Q(asistencia__usuario__estado=Usuario.ESTADO_EXONERADO)

    eventos_qs = Evento.objects.filter(
        pendientes_filter
    ).annotate(
        total_pendientes=Count('asistencia', filter=pendientes_filter, distinct=True)
    ).filter(
        total_pendientes__gt=0
    ).order_by('-fecha', '-id')

    eventos_list = []
    total_pendientes_global = 0

    for ev in eventos_qs:
        total_pendientes_global += ev.total_pendientes
        eventos_list.append({
            'id': ev.id,
            'nombre': ev.nombre,
            'fecha': ev.fecha.strftime('%d/%m/%Y'),
            'hora_ingreso': ev.hora_ingreso.strftime('%H:%M') if ev.hora_ingreso else '08:00',
            'tipo': ev.tipo_nombre,
            'color_badge': ev.color_badge,
            'activo': ev.activo,
            'total_pendientes': ev.total_pendientes,
        })

    return JsonResponse({
        'success': True,
        'total_pendientes_global': total_pendientes_global,
        'eventos': eventos_list,
    })


@login_required
def api_evento_pendientes(request, evento_id):
    """
    Retorna el listado de comuneros con ingreso pero sin salida de un evento específico.
    """
    if not (request.user.is_superuser or request.user.is_staff or request.user.has_perm('asistencia.can_manage_users')):
        return JsonResponse({'error': 'No tiene permisos para acceder a la regularización.'}, status=403)

    evento = get_object_or_404(Evento, id=evento_id)

    pendientes_qs = Asistencia.objects.filter(
        evento=evento,
        hora_ingreso__isnull=False,
        hora_salida__isnull=True,
        es_justificada=False,
    ).exclude(
        usuario__estado=Usuario.ESTADO_EXONERADO
    ).select_related('usuario').order_by('usuario__apellido', 'usuario__nombre')

    pendientes_data = []
    for a in pendientes_qs:
        pendientes_data.append({
            'asistencia_id': a.id,
            'usuario_id': a.usuario.id,
            'dni': a.usuario.dni,
            'nombre_completo': f"{a.usuario.apellido}, {a.usuario.nombre}",
            'foto_url': a.usuario.foto_perfil_url,
            'hora_ingreso': a.hora_ingreso.strftime('%H:%M') if a.hora_ingreso else '--',
            'puntualidad': a.get_puntualidad_display() if a.puntualidad else 'No aplica',
            'es_tarde': a.puntualidad == Asistencia.PUNTUALIDAD_TARDE,
        })

    now_local = timezone.localtime(timezone.now())
    if evento.fecha == now_local.date():
        sugerencia_hora = now_local.strftime('%H:%M')
    else:
        if evento.hora_ingreso:
            dt_base = datetime.combine(evento.fecha, evento.hora_ingreso) + timedelta(hours=4)
            sugerencia_hora = dt_base.strftime('%H:%M')
        else:
            sugerencia_hora = "13:00"

    return JsonResponse({
        'success': True,
        'evento': {
            'id': evento.id,
            'nombre': evento.nombre,
            'fecha': evento.fecha.strftime('%d/%m/%Y'),
            'hora_ingreso': evento.hora_ingreso.strftime('%H:%M') if evento.hora_ingreso else '08:00',
            'tipo': evento.tipo_nombre,
            'color_badge': evento.color_badge,
        },
        'sugerencia_hora_salida': sugerencia_hora,
        'total': len(pendientes_data),
        'pendientes': pendientes_data,
    })


@login_required
def regularizar_pendientes_evento(request):
    """
    Endpoint POST para marcar las asistencias pendientes de un evento como completadas.
    """
    if not (request.user.is_superuser or request.user.is_staff or request.user.has_perm('asistencia.can_manage_users')):
        return JsonResponse({'error': 'No tiene permisos para regularizar asistencias.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido. Se requiere POST.'}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'error': 'Formato JSON inválido.'}, status=400)

    evento_id = data.get('evento_id')
    if not evento_id:
        return JsonResponse({'error': 'Debe especificar el ID del evento.'}, status=400)

    evento = get_object_or_404(Evento, id=evento_id)
    marcar_todos = bool(data.get('marcar_todos', False))
    asistencia_ids = data.get('asistencia_ids')
    hora_salida_str = (data.get('hora_salida') or '').strip()
    observacion = (data.get('observacion') or '').strip()

    if not marcar_todos and not asistencia_ids:
        return JsonResponse({'error': 'Debe seleccionar al menos un comunero o marcar todos.'}, status=400)

    hora_salida_obj = None
    if hora_salida_str:
        try:
            hora_salida_obj = datetime.strptime(hora_salida_str, '%H:%M').time()
        except ValueError:
            try:
                hora_salida_obj = datetime.strptime(hora_salida_str, '%H:%M:%S').time()
            except ValueError:
                hora_salida_obj = None

    if not hora_salida_obj:
        hora_salida_obj = timezone.localtime(timezone.now()).time()

    from .services import AsistenciaService
    ids_to_process = None if marcar_todos else asistencia_ids
    total_updated, _ = AsistenciaService.regularizar_pendientes(
        evento=evento,
        asistencia_ids=ids_to_process,
        hora_salida=hora_salida_obj,
        observacion=observacion,
        request_user=request.user,
    )

    return JsonResponse({
        'success': True,
        'count': total_updated,
        'message': f'Se regularizó la asistencia de {total_updated} comunero(s) exitosamente como completos.',
    })

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_pdf(request):
    form = FiltroAsistenciaForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    data = get_filtered_attendance_data(filters)
    
    # Usar la lista unificada
    unified_list = data.get('unified_report', [])
    
    # Calcular estadísticas detalladas
    total_padron = len(unified_list) # Usar el padrón del reporte (Activos + Exon)
    asistencias_puras = sum(1 for item in unified_list if not getattr(item, 'is_absent', False))
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    total_asistentes_efectivos = asistencias_puras + justificadas
    total_inasistentes_reales = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    
    porcentaje_asistencia = (total_asistentes_efectivos / total_padron * 100) if total_padron > 0 else 0

    context = {
        'unified_list': unified_list,
        'total_usuarios': total_padron,
        'total_asistentes': total_asistentes_efectivos,
        'asistencias_puras': asistencias_puras,
        'justificadas': justificadas,
        'total_inasistentes': total_inasistentes_reales,
        'porcentaje_asistencia': porcentaje_asistencia,
        'logo_base64': get_logo_base64(),
        'sistema_config': ConfiguracionSistema.objects.first(),
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'filtros': {
            'fecha_inicio': filters.get('fecha_inicio').strftime('%d/%m/%Y') if filters.get('fecha_inicio') else 'Inicio',
            'fecha_fin': filters.get('fecha_fin').strftime('%d/%m/%Y') if filters.get('fecha_fin') else 'Hoy',
            'evento': filters.get('evento'),
            'dni': filters.get('dni') or 'Todos'
        }
    }
    
    return generate_pdf_report('asistencia/reporte_asistencias.html', context, "reporte_asistencias.pdf")

def _get_user_unified_attendance_data(usuario, sistema_config=None):
    """
    Construye de manera unificada y coherente el historial de asistencias de un comunero
    para reportes individuales (PDF y Excel), asegurando que confirmaciones y regularizaciones
    figuren como ASISTIÓ y no como PENDIENTE.
    """
    if sistema_config is None:
        sistema_config = ConfiguracionSistema.objects.first()
    tardanza_activa = bool(sistema_config and sistema_config.tardanza_activa)

    todos_eventos = list(Evento.objects.all().order_by('-fecha', '-hora_ingreso'))
    asistencias = list(Asistencia.objects.filter(usuario=usuario).select_related('evento', 'ubicacion'))

    def _is_better_asistencia(new_a, old_a):
        # 1. Preferir con hora de salida
        if bool(new_a.hora_salida) != bool(old_a.hora_salida):
            return bool(new_a.hora_salida)
        # 2. Preferir confirmada por administración/mesa
        if bool(getattr(new_a, 'confirmada', False)) != bool(getattr(old_a, 'confirmada', False)):
            return bool(getattr(new_a, 'confirmada', False))
        # 3. Preferir justificada aprobada
        if bool(getattr(new_a, 'es_justificada', False)) != bool(getattr(old_a, 'es_justificada', False)):
            return bool(getattr(new_a, 'es_justificada', False))
        # 4. Preferir con hora de ingreso
        if bool(new_a.hora_ingreso) != bool(old_a.hora_ingreso):
            return bool(new_a.hora_ingreso)
        return False

    asistencia_map = {}
    for a in asistencias:
        ev_id = a.evento_id
        if not ev_id and a.fecha:
            matching_ev = next((ev for ev in todos_eventos if ev.fecha == a.fecha), None)
            if matching_ev:
                ev_id = matching_ev.id
        if ev_id:
            if ev_id not in asistencia_map:
                asistencia_map[ev_id] = a
            else:
                existing = asistencia_map[ev_id]
                if _is_better_asistencia(a, existing):
                    asistencia_map[ev_id] = a

    justificaciones = list(Justificacion.objects.filter(usuario=usuario, estado='APROBADO'))
    justificacion_map = {j.evento_id: j for j in justificaciones}

    unified_list = []
    for evento in todos_eventos:
        if evento.id in asistencia_map:
            item = asistencia_map[evento.id]
            item.es_justificada = bool(getattr(item, 'es_justificada', False))
            item.is_late_absent = bool(
                tardanza_activa
                and getattr(item, 'puntualidad', None) == Asistencia.PUNTUALIDAD_TARDE
                and item.usuario.estado != Usuario.ESTADO_EXONERADO
            )
            is_empty_or_orphan = (
                not getattr(item, 'hora_ingreso', None)
                and not getattr(item, 'hora_salida', None)
                and not getattr(item, 'confirmada', False)
                and not item.es_justificada
                and item.usuario.estado != Usuario.ESTADO_EXONERADO
            )
            item.is_absent = bool(item.es_justificada or item.is_late_absent or is_empty_or_orphan)
            unified_list.append(item)

        elif evento.id in justificacion_map:
            justificacion = justificacion_map[evento.id]
            mock_item = type('MockAsistencia', (object,), {
                'fecha': evento.fecha,
                'hora_ingreso': None,
                'hora_salida': None,
                'evento': evento,
                'ubicacion': None,
                'usuario': usuario,
                'is_absent': True,
                'es_justificada': True,
                'is_late_absent': False,
                'confirmada': True,
                'justificacion_obs': justificacion.motivo,
                'estado_display': 'JUSTIFICADA'
            })
            unified_list.append(mock_item)

        elif usuario.estado == Usuario.ESTADO_EXONERADO:
            mock_item = type('MockAsistencia', (object,), {
                'fecha': evento.fecha,
                'hora_ingreso': None,
                'hora_salida': None,
                'evento': evento,
                'ubicacion': None,
                'usuario': usuario,
                'is_absent': False,
                'es_justificada': False,
                'is_late_absent': False,
                'confirmada': True,
                'justificacion_obs': 'Exonerado por tercera edad',
                'estado_display': 'EXONERADO'
            })
            unified_list.append(mock_item)

        else:
            mock_item = type('MockAsistencia', (object,), {
                'fecha': evento.fecha,
                'hora_ingreso': None,
                'hora_salida': None,
                'evento': evento,
                'ubicacion': None,
                'usuario': usuario,
                'is_absent': True,
                'es_justificada': False,
                'is_late_absent': False,
                'confirmada': False,
                'justificacion_obs': 'No asistió (Sin justificación)',
                'estado_display': 'FALTA'
            })
            unified_list.append(mock_item)

    total_eventos = len(todos_eventos)
    asistencias_confirmadas = sum(
        1 for item in unified_list 
        if not getattr(item, 'is_absent', False) and 
        (getattr(item, 'hora_salida', None) or getattr(item, 'confirmada', False) or item.usuario.estado == Usuario.ESTADO_EXONERADO)
    )
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    faltas = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    pendientes = sum(
        1 for item in unified_list 
        if not getattr(item, 'is_absent', False) and 
        not getattr(item, 'hora_salida', None) and 
        not getattr(item, 'confirmada', False) and 
        item.usuario.estado != Usuario.ESTADO_EXONERADO
    )

    faltas_asambleas = sum(
        1 for item in unified_list 
        if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False)
        and not getattr(getattr(item, 'evento', None), 'es_faena', False)
    )
    faltas_faenas = sum(
        1 for item in unified_list 
        if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False)
        and getattr(getattr(item, 'evento', None), 'es_faena', False)
    )

    total_participaciones = asistencias_confirmadas + justificadas
    score_asistencia = (total_participaciones / total_eventos * 100) if total_eventos > 0 else 0

    stats = {
        'total_eventos': total_eventos,
        'asistencias': asistencias_confirmadas,
        'justificadas': justificadas,
        'faltas': faltas,
        'faltas_asambleas': faltas_asambleas,
        'faltas_faenas': faltas_faenas,
        'pendientes': pendientes,
        'score': round(score_asistencia, 1),
    }

    return todos_eventos, unified_list, stats


@login_required
def descargar_reporte_usuario_pdf(request, dni):
    # Seguridad y prevención de fuga de datos: acceso exclusivo a administradores o al titular del DNI
    es_admin = request.user.has_perm('asistencia.can_manage_users')
    es_duenio = hasattr(request.user, 'usuario') and str(request.user.usuario.dni) == str(dni)
    if not (es_admin or es_duenio):
        raise PermissionDenied("No tienes autorización para acceder a los reportes de otros comuneros.")

    usuario = get_object_or_404(Usuario, dni=dni)
    sistema_config = ConfiguracionSistema.objects.first()

    todos_eventos, unified_list, stats = _get_user_unified_attendance_data(usuario, sistema_config)

    # Racha actual y racha máxima
    racha_actual = 0
    for item in unified_list:
        if not getattr(item, 'is_absent', False) or getattr(item, 'es_justificada', False):
            racha_actual += 1
        else:
            if item.fecha < timezone.now().date():
                break
            break

    racha_maxima = 0
    temp_racha = 0
    for item in reversed(unified_list):
        if not getattr(item, 'is_absent', False) or getattr(item, 'es_justificada', False):
            temp_racha += 1
            if temp_racha > racha_maxima:
                racha_maxima = temp_racha
        else:
            temp_racha = 0

    stats['racha_actual'] = racha_actual
    stats['racha_maxima'] = racha_maxima
    ultimas_asistencias = unified_list[:5]

    context = {
        'usuario': usuario,
        'unified_list': unified_list,
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64(),
        'sistema_config': sistema_config,
        'stats': stats,
        'ultimas_asistencias': ultimas_asistencias,
    }

    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar reporte asistencias usuario PDF",
        descripcion=f"{request.user.username} descargó el reporte de asistencias de {usuario.nombre} {usuario.apellido} (DNI: {usuario.dni}) en PDF."
    )

    return generate_pdf_report('asistencia/reporte_asistencias_usuario.html', context, f"reporte_asistencias_{usuario.dni}.pdf")


@login_required
def descargar_reporte_usuario_excel(request, dni):
    """
    Genera un Excel individual completo de asistencias e inasistencias para un comunero específico.
    """
    # Seguridad y prevención de fuga de datos: acceso exclusivo a administradores o al titular del DNI
    es_admin = request.user.has_perm('asistencia.can_manage_users')
    es_duenio = hasattr(request.user, 'usuario') and str(request.user.usuario.dni) == str(dni)
    if not (es_admin or es_duenio):
        raise PermissionDenied("No tienes autorización para acceder a los reportes de otros comuneros.")

    from .utils.reports import generate_user_attendance_excel
    usuario = get_object_or_404(Usuario, dni=dni)
    sistema_config = ConfiguracionSistema.objects.first()

    todos_eventos, unified_list, stats = _get_user_unified_attendance_data(usuario, sistema_config)

    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar reporte asistencias usuario Excel",
        descripcion=f"{request.user.username} descargó el reporte de asistencias de {usuario.nombre} {usuario.apellido} (DNI: {usuario.dni}) en Excel."
    )

    return generate_user_attendance_excel(usuario, unified_list, stats, sistema_config)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_evento_pdf(request, evento_id):
    evento = get_object_or_404(Evento, id=evento_id)
    form = FiltroAsistenciaForm(request.GET or None)
    
    filters = form.cleaned_data if form.is_valid() else {}
    context, _ = _build_event_report_context(evento, filters=filters)
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar reporte evento PDF",
        descripcion=f"{request.user.username} descargó el reporte del evento {evento.nombre} en PDF."
    )
    
    return generate_pdf_report('asistencia/reporte_evento.html', context, f"reporte_evento_{evento.id}.pdf")

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
@require_POST
def cerrar_evento(request, evento_id):
    evento = get_object_or_404(Evento, id=evento_id)
    if not evento.activo:
        return JsonResponse({'error': 'El evento ya se encuentra cerrado.'}, status=400)

    before = {'activo': evento.activo}
    evento.activo = False
    evento.save(update_fields=['activo'])

    context, unified_list = _build_event_report_context(evento)
    exonerado_alert = context.get('exonerado_alert', {})
    if exonerado_alert.get('has_mismatch'):
        evento.activo = True
        evento.save(update_fields=['activo'])
        return JsonResponse({
            'error': (
                "Inconsistencia detectada en exonerados. "
                f"Padrón: {exonerado_alert.get('total_exonerados_padron', 0)} vs "
                f"presentes: {exonerado_alert.get('exonerados_presentes', 0)}. "
                "Revise antes de cerrar el evento."
            ),
            'code': 'EXONERADO_MISMATCH',
            'alert': exonerado_alert,
        }, status=409)
    timestamp = timezone.localtime(timezone.now()).strftime('%Y%m%d_%H%M%S')
    closure_dir = os.path.join(settings.MEDIA_ROOT, 'cierres_evento')
    os.makedirs(closure_dir, exist_ok=True)

    pdf_filename = f"resumen_evento_{evento.id}_{timestamp}.pdf"
    excel_filename = f"resumen_evento_{evento.id}_{timestamp}.xlsx"
    pdf_path = os.path.join(closure_dir, pdf_filename)
    excel_path = os.path.join(closure_dir, excel_filename)

    pdf_response = generate_pdf_report('asistencia/reporte_evento.html', context, pdf_filename)
    if pdf_response.status_code >= 400:
        evento.activo = True
        evento.save(update_fields=['activo'])
        return JsonResponse({'error': 'No se pudo generar el resumen PDF del cierre.'}, status=500)

    with open(pdf_path, 'wb') as pdf_file:
        pdf_file.write(pdf_response.content)

    excel_response = generate_attendance_excel(unified_list, filename=excel_filename)
    with open(excel_path, 'wb') as excel_file:
        excel_file.write(excel_response.content)

    after = {'activo': evento.activo}
    log_critical_change(
        request.user,
        f'Evento:{evento.id}',
        before,
        after,
        action_label='Cierre de evento con resumen automatico',
    )
    LogAccion.objects.create(
        usuario=request.user,
        accion="Cierre de evento",
        descripcion=(
            f"Se cerró el evento {evento.nombre}. PDF: {pdf_filename}. "
            f"Excel: {excel_filename}. Tardanzas: {context.get('total_tardanzas', 0)}."
        ),
    )

    return JsonResponse({
        'message': 'Evento cerrado y resumen generado correctamente.',
        'pdf_url': f"{settings.MEDIA_URL}cierres_evento/{pdf_filename}",
        'excel_url': f"{settings.MEDIA_URL}cierres_evento/{excel_filename}",
    })


@login_required
def buscar_usuario_dni(request):
    dni = request.GET.get('dni', '')
    if len(dni) >= 8:
        usuario = Usuario.objects.filter(dni=dni).first()
        if usuario:
            safe_nombre = escape(f"{usuario.nombre} {usuario.apellido}")
            return HttpResponse(f'<div class="mt-2 text-[10px] font-black text-emerald-600 animate-pulse uppercase tracking-widest"><i class="bi bi-check-circle-fill"></i> Socio: {safe_nombre}</div>')
        else:
            return HttpResponse('<div class="mt-2 text-[10px] font-black text-red-500 uppercase tracking-widest"><i class="bi bi-x-circle-fill"></i> Socio no encontrado</div>')
    return HttpResponse('')

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_filtrado_pdf(request):
    """Genera un PDF con los filtros aplicados desde la página de historial"""
    form = FiltroAsistenciaForm(request.GET or None)
    
    filters = form.cleaned_data if form.is_valid() else {}
    effective_filters = filters.copy()
    auto_event_from_range = False
    eventos_en_rango = Evento.objects.none()

    if effective_filters.get('fecha_inicio') and effective_filters.get('fecha_fin'):
        eventos_en_rango = Evento.objects.filter(
            fecha__range=[effective_filters.get('fecha_inicio'), effective_filters.get('fecha_fin')]
        ).order_by('fecha', 'hora_ingreso')

    data = get_filtered_attendance_data(effective_filters)
    unified_list = data.get('unified_report', [])
    
    # Calcular estadísticas
    if effective_filters.get('evento'):
        total_registros = Usuario.objects.filter(
            estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]
        ).count()
    else:
        total_registros = len(unified_list)

    confirmadas = sum(
        1 for item in unified_list 
        if not getattr(item, 'is_absent', False) and 
        (getattr(item, 'hora_salida', None) or getattr(item, 'confirmada', False) or item.usuario.estado == 'EXONERADO')
    )
    pendientes = sum(
        1 for item in unified_list 
        if not getattr(item, 'is_absent', False) and 
        not getattr(item, 'hora_salida', None) and 
        not getattr(item, 'confirmada', False) and 
        item.usuario.estado != 'EXONERADO'
    )
    inasistencias = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))

    grouped_items = []
    grouped_map = {}
    for item in unified_list:
        evento_obj = getattr(item, 'evento', None)
        evento_key = getattr(evento_obj, 'id', None) or f"sin-evento-{getattr(item, 'fecha', None)}"
        if evento_key not in grouped_map:
            grouped_map[evento_key] = {
                'evento': evento_obj,
                'fecha': getattr(item, 'fecha', None),
                'items': [],
            }
            grouped_items.append(grouped_map[evento_key])
        grouped_map[evento_key]['items'].append(item)
    
    context = {
        'unified_list': unified_list,
        'grouped_items': grouped_items,
        'total_registros': total_registros,
        'confirmadas': confirmadas,
        'pendientes': pendientes,
        'inasistencias': inasistencias,
        'justificadas': justificadas,
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64(),
        'sistema_config': ConfiguracionSistema.objects.first(),
        'is_event_scope': bool(effective_filters.get('evento')),
        'auto_event_from_range': auto_event_from_range,
        'filtros': {
            'dni': effective_filters.get('dni') or 'Todos',
            'fecha_inicio': effective_filters.get('fecha_inicio').strftime('%d/%m/%Y') if effective_filters.get('fecha_inicio') else 'Sin filtro',
            'fecha_fin': effective_filters.get('fecha_fin').strftime('%d/%m/%Y') if effective_filters.get('fecha_fin') else 'Sin filtro',
            'evento': (
                effective_filters.get('evento').nombre
                if effective_filters.get('evento')
                else (
                    f"{eventos_en_rango.count()} eventos dentro del rango"
                    if eventos_en_rango.exists()
                    else 'Todos los eventos'
                )
            ),
            'ubicacion': effective_filters.get('ubicacion').nombre if effective_filters.get('ubicacion') else 'Todas',
            'estado': dict(form.fields['estado'].choices).get(effective_filters.get('estado', ''), 'Todos') if effective_filters.get('estado') else 'Todos',
        }
    }
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar reporte filtrado PDF",
        descripcion=f"{request.user.username} descargó un reporte filtrado en PDF."
    )
    
    return generate_pdf_report('asistencia/reporte_filtrado.html', context, "reporte_filtrado.pdf")

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def importar_usuarios(request):
    form = ImportarUsuariosForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        csv_file = TextIOWrapper(request.FILES['archivo_csv'].file, encoding='utf-8')
        reader = csv.DictReader(csv_file)
        errores = []
        usuarios_importados = 0

        expected_columns = {'nombre', 'apellido', 'dni'}
        csv_columns = set(reader.fieldnames)
        if not expected_columns.issubset(csv_columns):
            missing = expected_columns - csv_columns
            messages.error(request, f"Faltan las siguientes columnas en el CSV: {', '.join(missing)}")
            return render(request, 'asistencia/importar_usuarios.html', {
                'form': form,
                'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
            })

        for row in reader:
            try:
                nombre = row['nombre']
                apellido = row['apellido']
                dni = row['dni']
                fecha_nacimiento = None
                estado = row.get('estado', Usuario.ESTADO_ACTIVO)

                if 'fecha_nacimiento' in row and row['fecha_nacimiento']:
                    try:
                        fecha_nacimiento = datetime.strptime(row['fecha_nacimiento'], '%Y-%m-%d').date()
                        today = date.today()
                        age = today.year - fecha_nacimiento.year - ((today.month, today.day) < (fecha_nacimiento.month, fecha_nacimiento.day))
                        if age >= 65:
                            estado = Usuario.ESTADO_EXONERADO
                    except ValueError:
                        errores.append(f"Fila con DNI {dni}: Formato de fecha de nacimiento inválido (use YYYY-MM-DD).")
                        continue

                if 'estado' in row and row['estado']:
                    if row['estado'] not in dict(Usuario.ESTADOS).keys():
                        errores.append(f"Fila con DNI {dni}: Estado inválido. Debe ser ACTIVO, PASIVO o EXONERADO.")
                        continue

                usuario = Usuario.objects.create(
                    nombre=nombre,
                    apellido=apellido,
                    dni=dni,
                    fecha_nacimiento=fecha_nacimiento,
                    estado=estado
                )
                LogAccion.objects.create(
                    usuario=request.user,
                    accion="Importar usuario",
                    descripcion=f"{request.user.username} importó al usuario {usuario.nombre} {usuario.apellido} (DNI: {usuario.dni})."
                )
                usuarios_importados += 1
            except Exception as e:
                errores.append(f"Fila con DNI {row.get('dni', 'desconocido')}: {str(e)}")
                continue

        context = {
            'form': form,
            'success': True,
            'usuarios_importados': usuarios_importados,
            'errores': errores,
            'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
        }
        return render(request, 'asistencia/importar_usuarios.html', context)

    context = {
        'form': form,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    }
    return render(request, 'asistencia/importar_usuarios.html', context)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def registrar_usuario(request):
    if request.method == 'POST':
        form = UsuarioRegistroForm(request.POST, request.FILES)
        if form.is_valid():
            # Form's save() method now handles EXONERADO logic automatically
            usuario = form.save(commit=False)
            user = User.objects.create_user(
                username=usuario.dni,
                password=form.cleaned_data['password']
            )
            usuario.user = user
            
            if form.cleaned_data.get('es_escaneador'):
                from django.contrib.auth.models import Group
                try:
                    group = Group.objects.get(name='Escaneadores')
                    user.groups.add(group)
                    user.is_staff = True  # Opcional: permite que vean el dashboard si tienen otros permisos
                    user.save()
                except Group.DoesNotExist:
                    pass

            usuario.save()
            LogAccion.objects.create(
                usuario=request.user,
                accion="Registrar usuario",
                descripcion=f"{request.user.username} registró al usuario {usuario.nombre} {usuario.apellido} (DNI: {usuario.dni})."
            )
            return redirect('lista_usuarios')
    else:
        form = UsuarioRegistroForm()
    context = {
        'form': form,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    }
    return render(request, 'asistencia/registrar_usuario.html', context)

@login_required
def perfil_usuario(request):
    try:
        usuario = Usuario.objects.get(user=request.user)
        asistencias = Asistencia.objects.filter(usuario=usuario).order_by('-fecha', '-hora_ingreso')
        sistema_config = ConfiguracionSistema.objects.first()
        tardanza_activa = bool(sistema_config and sistema_config.tardanza_activa)
        for asistencia in asistencias:
            asistencia.is_late_absent = bool(
                tardanza_activa
                and getattr(asistencia, 'puntualidad', None) == Asistencia.PUNTUALIDAD_TARDE
                and asistencia.usuario.estado != Usuario.ESTADO_EXONERADO
            )
        context = {
            'usuario': usuario,
            'asistencias': asistencias,
            'sistema_config': sistema_config,
            'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
        }
        return render(request, 'asistencia/perfil_usuario.html', context)
    except Usuario.DoesNotExist:
        context = {
            'error': 'No se encontró un usuario asociado a tu cuenta.',
            'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
        }
        return render(request, 'asistencia/perfil_usuario.html', context)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
@require_POST
def actualizar_foto_rapida(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    error_message = None

    if request.FILES.get('foto'):
        try:
            usuario.foto_perfil = request.FILES['foto']
            usuario.full_clean()
            usuario.save()  # El modelo ya procesa y normaliza la imagen

            LogAccion.objects.create(
                usuario=request.user,
                accion="Actualización de foto rápida",
                descripcion=f"{request.user.username} actualizó la foto de {usuario.nombre} {usuario.apellido} desde la lista."
            )
        except ValidationError as exc:
            mensajes = []
            if hasattr(exc, 'message_dict'):
                for field_errors in exc.message_dict.values():
                    mensajes.extend(field_errors)
            elif hasattr(exc, 'messages'):
                mensajes.extend(exc.messages)
            error_message = " ".join(mensajes) or "No se pudo validar la imagen."
        except Exception:
            logger.exception("Error al actualizar foto rápida del usuario %s", usuario.dni)
            error_message = "No se pudo guardar la fotografía en este momento."
    else:
        error_message = "No se recibió ninguna fotografía."

    avatar_html = (
        f'<img src="{escape(usuario.foto_perfil_url)}" alt="Foto" '
        'class="h-12 w-12 rounded-2xl object-cover border border-slate-200 shadow-sm">'
        if usuario.foto_perfil else
        (
            '<div class="h-12 w-12 rounded-2xl bg-gradient-to-br from-slate-100 to-slate-200 '
            'text-slate-500 flex items-center justify-center font-black text-sm border border-slate-200">'
            f'{escape(usuario.nombre[:1])}{escape(usuario.apellido[:1])}'
            '</div>'
        )
    )

    html = f"""
    <div class="relative group h-12 w-12 shrink-0" id="avatar-container-{usuario.dni}">
        {avatar_html}
        <form hx-post="{reverse('actualizar_foto_rapida', args=[usuario.dni])}"
              hx-encoding="multipart/form-data"
              hx-target="#avatar-container-{usuario.dni}"
              hx-swap="outerHTML"
              class="absolute inset-0 z-10">
            <label class="absolute inset-0 bg-slate-900/60 rounded-2xl flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer ring-2 ring-primary-500 ring-offset-1 ring-offset-white" title="Actualizar foto">
                <i class="bi bi-camera-fill text-white text-lg drop-shadow-md"></i>
                <input type="file" name="foto" accept="image/*" hx-trigger="change" class="hidden"
                       onchange="if (this.files && this.files.length) this.form.requestSubmit();">
            </label>
        </form>
    </div>
    """
    response = HttpResponse(html)
    if error_message:
        response["HX-Trigger"] = json.dumps({
            "photoUploadError": {"message": error_message}
        })
    return response


@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
@require_POST
def solicitar_descarga_carnets(request):
    activa = _get_active_carnet_download_request(request.user)
    if activa:
        return JsonResponse(
            {
                'ok': False,
                'message': 'Ya tienes una generación en curso. Espera a que termine para lanzar otra.',
                'solicitud_id': activa.id,
            },
            status=409,
        )

    _cleanup_previous_carnet_requests(request.user)
    solicitud = SolicitudDescargaCarnets.objects.create(solicitado_por=request.user)
    try:
        _launch_carnets_generation_job(solicitud.id)
    except Exception as exc:
        logger.exception("No se pudo lanzar la generación asíncrona de carnets")
        solicitud.estado = SolicitudDescargaCarnets.ESTADO_ERROR
        solicitud.mensaje_error = str(exc)
        solicitud.fecha_fin = timezone.now()
        solicitud.save(update_fields=['estado', 'mensaje_error', 'fecha_fin'])
        return JsonResponse(
            {
                'ok': False,
                'message': 'No se pudo iniciar la generación del archivo.',
            },
            status=500,
        )

    LogAccion.objects.create(
        usuario=request.user,
        accion="Solicitar descarga carnets",
        descripcion=(
            f"{request.user.username} solicitó la generación asíncrona "
            f"del ZIP de carnets #{solicitud.id}."
        )
    )
    return JsonResponse(
        {
            'ok': True,
            'message': 'La generación empezó en segundo plano. Te avisaremos aquí mismo cuando el ZIP esté listo.',
            'solicitud_id': solicitud.id,
        }
    )


@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def estado_descargas_carnets(request):
    context = {
        'solicitudes_descarga_carnets': _get_recent_carnet_download_requests(request.user, limit=8),
        'solicitud_descarga_activa': _get_active_carnet_download_request(request.user),
    }
    return render(request, 'asistencia/partials/download_jobs.html', context)


@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_archivo_carnets(request, solicitud_id):
    solicitud = get_object_or_404(
        SolicitudDescargaCarnets,
        pk=solicitud_id,
        solicitado_por=request.user,
    )
    if solicitud.estado != SolicitudDescargaCarnets.ESTADO_LISTO or not solicitud.archivo_zip:
        raise Http404("El archivo solicitado todavía no está disponible.")
    if not solicitud.archivo_zip.storage.exists(solicitud.archivo_zip.name):
        raise Http404("El archivo ZIP ya no existe en el servidor.")

    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar ZIP carnets listo",
        descripcion=(
            f"{request.user.username} descargó el archivo preparado "
            f"para la solicitud #{solicitud.id}."
        )
    )
    return FileResponse(
        solicitud.archivo_zip.open('rb'),
        as_attachment=True,
        filename=solicitud.nombre_archivo or os.path.basename(solicitud.archivo_zip.name),
        content_type='application/zip',
    )


@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_todos_carnets_pdf(request):
    """
    Respaldo síncrono de la descarga completa.
    La UI principal usa la versión asíncrona para evitar timeouts.
    """
    payload = _generate_carnets_zip_payload()

    LogAccion.objects.create(
        usuario=request.user,
        accion="Descarga carnets ZIP síncrona",
        descripcion=(
            f"{request.user.username} finalizó la descarga ZIP con "
            f"{payload['total_carnets_con_foto']} carnets con foto, "
            f"{payload['total_carnets_sin_foto']} carnets sin foto, "
            f"{payload['total_usuarios_sin_foto']} en reporte sin foto, "
            f"{payload['total_incidencias']} incidencias técnicas registradas, "
            f"archivos {', '.join(payload['zip_members']) or 'ninguno'} "
            f"en {payload['duration_seconds']:.2f}s."
        )
    )

    response = HttpResponse(payload['zip_bytes'], content_type='application/zip')
    response['Content-Disposition'] = (
        f'attachment; filename="{payload["zip_filename"]}"; '
        f"filename*=UTF-8''{quote(payload['zip_filename'])}"
    )
    response['Content-Length'] = len(payload['zip_bytes'])
    return response

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def entregar_carnet(request, usuario_id):
    usuario = get_object_or_404(Usuario, id=usuario_id)
    if request.method == 'POST':
        form = CarnetForm(request.POST)
        if form.is_valid():
            nuevo_carnet = form.save(commit=False)
            nuevo_carnet.usuario = usuario
            nuevo_carnet.entregado_por = request.user
            nuevo_carnet.save()
            messages.success(request, f"Carnet '{nuevo_carnet.motivo}' registrado exitosamente para {usuario.nombre} {usuario.apellido}.")
            return redirect('detalle_usuario', dni=usuario.dni)
    else:
        form = CarnetForm()
    
    context = {
        'form': form,
        'usuario': usuario,
    }
    return render(request, 'asistencia/registrar_carnet.html', context)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def modulo_gestion_carnets(request):
    usuario_seleccionado = None
    carnet_activo = None

    if request.method == 'POST':
        usuario_id = request.POST.get('usuario_id')
        accion = request.POST.get('accion')

        if not (usuario_id and accion):
            messages.error(request, "Solicitud incompleta: faltan datos para registrar la emisión.")
            return redirect(reverse('modulo_gestion_carnets'))

        motivo_map = {
            'primer_carnet': 'Primer Carnet',
            'reposicion_perdida': 'Reposición por Pérdida/Robo',
            'reposicion_deterioro': 'Reposición por Deterioro',
            'renovacion': 'Renovación por Vencimiento'
        }
        if accion not in motivo_map:
            messages.error(request, "Acción de emisión no válida.")
            return redirect(reverse('modulo_gestion_carnets'))

        try:
            with transaction.atomic():
                usuario = Usuario.objects.select_for_update().get(id=usuario_id)
                carnet_activo_usuario = (
                    HistorialCarnet.objects.select_for_update()
                    .filter(usuario=usuario, estado='Activo')
                    .first()
                )

                if not usuario.foto_perfil:
                    messages.error(
                        request,
                        f"No se puede emitir carnet para {usuario.nombre} {usuario.apellido} sin fotografía de perfil."
                    )
                    return redirect(f"{reverse('modulo_gestion_carnets')}?usuario_id={usuario.id}")

                if accion == 'primer_carnet' and carnet_activo_usuario:
                    messages.error(
                        request,
                        f"{usuario.nombre} {usuario.apellido} ya cuenta con un carnet activo."
                    )
                    return redirect(f"{reverse('modulo_gestion_carnets')}?usuario_id={usuario.id}")

                if accion in {'reposicion_perdida', 'reposicion_deterioro', 'renovacion'} and not carnet_activo_usuario:
                    messages.error(
                        request,
                        f"No se puede registrar {motivo_map[accion].lower()} porque el socio no tiene carnet activo."
                    )
                    return redirect(f"{reverse('modulo_gestion_carnets')}?usuario_id={usuario.id}")

                motivo = motivo_map[accion]
                nuevo_carnet = HistorialCarnet(
                    usuario=usuario,
                    motivo=motivo,
                    estado='Activo',
                    estado_entrega=HistorialCarnet.ESTADO_ENTREGA_PENDIENTE,
                    entregado_por=request.user,
                )
                nuevo_carnet.save()

                LogAccion.objects.create(
                    usuario=request.user,
                    accion="Emisión de carnet físico",
                    descripcion=(
                        f"{request.user.username} registró '{motivo}' para "
                        f"{usuario.nombre} {usuario.apellido} (DNI: {usuario.dni})."
                    ),
                )
                messages.success(
                    request,
                    f"¡Éxito! Se ha emitido físicamente un '{motivo}' para {usuario.nombre} {usuario.apellido}."
                )

                if accion in {'reposicion_perdida', 'reposicion_deterioro', 'renovacion'}:
                    usuario.rotate_qr()

                return redirect(f"{reverse('modulo_gestion_carnets')}?usuario_id={usuario.id}")
        except Usuario.DoesNotExist:
            messages.error(request, "No se encontró el socio seleccionado.")
            return redirect(reverse('modulo_gestion_carnets'))

    usuario_id = request.GET.get('usuario_id')
    form = AdminCarnetForm()

    if usuario_id:
        try:
            usuario_seleccionado = Usuario.objects.get(id=usuario_id)
            carnet_activo = usuario_seleccionado.historial_carnets.filter(estado='Activo').first()
            form = AdminCarnetForm(initial={'usuario': usuario_seleccionado})
        except Usuario.DoesNotExist:
            pass

    from django.db.models import Q
    from django.core.paginator import Paginator
    
    # 0. Últimas Emisiones (HistorialCarnet)
    ultimos_carnets_qs = HistorialCarnet.objects.all().order_by('-fecha_emision', '-id')
    page_emisiones_number = request.GET.get('page_emisiones')
    paginator_emisiones = Paginator(ultimos_carnets_qs, 16)
    ultimos_carnets = paginator_emisiones.get_page(page_emisiones_number)
    
    # 1. Pendientes de Foto (No tienen foto)
    filtro_sin_foto = Q(foto_perfil__isnull=True) | Q(foto_perfil='')
    usuarios_sin_foto_qs = Usuario.objects.filter(filtro_sin_foto).order_by('apellido', 'nombre')
    count_sin_foto = usuarios_sin_foto_qs.count()
    
    page_number = request.GET.get('page')
    paginator = Paginator(usuarios_sin_foto_qs, 16)
    usuarios_sin_foto = paginator.get_page(page_number)
    
    # 2. Pendientes de Recoger/Entregar (Tienen foto pero NO tienen carnet activo)
    filtro_con_foto = ~Q(foto_perfil__isnull=True) & ~Q(foto_perfil='')
    usuarios_sin_recoger_qs = Usuario.objects.filter(filtro_con_foto).exclude(historial_carnets__estado='Activo').order_by('apellido', 'nombre')
    count_sin_recoger = usuarios_sin_recoger_qs.count()
    
    page_recoger_number = request.GET.get('page_recoger')
    paginator_recoger = Paginator(usuarios_sin_recoger_qs, 16)
    usuarios_sin_recoger = paginator_recoger.get_page(page_recoger_number)
    
    carnets_activos_count = HistorialCarnet.objects.filter(estado='Activo').count()
    
    if page_number:
        active_tab = 'pendientes'
    elif page_recoger_number:
        active_tab = 'recoger'
    elif page_emisiones_number:
        active_tab = 'emisiones'
    else:
        active_tab = 'emisiones'
    
    context = {
        'form': form,
        'ultimos_carnets': ultimos_carnets,
        'usuario_seleccionado': usuario_seleccionado,
        'carnet_activo': carnet_activo,
        'count_sin_foto': count_sin_foto,
        'usuarios_sin_foto': usuarios_sin_foto,
        'count_sin_recoger': count_sin_recoger,
        'usuarios_sin_recoger': usuarios_sin_recoger,
        'carnets_activos_count': carnets_activos_count,
        'active_tab': active_tab,
    }
    return render(request, 'asistencia/gestion_carnets.html', context)
