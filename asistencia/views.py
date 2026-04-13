from django.db import models
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import logout as auth_logout, authenticate, login as auth_login
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse, FileResponse
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
from .models import Usuario, Asistencia, Ubicacion, Evento, LogAccion, ConfiguracionSistema, Justificacion, HistorialCarnet
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
import tarfile
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

def landing_page(request):
    """
    Landing page pÃƒÂºblica para el sistema de asistencia de Quiulacocha.
    
    Si el usuario estÃƒÂ¡ autenticado, muestra un botÃƒÂ³n para ir al dashboard.
    Si no estÃƒÂ¡ autenticado, muestra la pÃƒÂ¡gina de bienvenida con CTA para login.
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
    
    # Calcular estadÃƒÂ­sticas basadas en los resultados filtrados
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
    Vista para mantener viva la sesiÃƒÂ³n del usuario mientras estÃƒÂ¡ en la pÃƒÂ¡gina de escaneo.
    """
    return JsonResponse({'status': 'success', 'message': 'SesiÃƒÂ³n mantenida activa'})


def healthcheck(request):
    """
    Endpoint pÃƒÂºblico y liviano para health checks de Docker/Hestia.
    No depende de sesiÃƒÂ³n ni de datos del sistema.
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
        return redirect('index')

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
        # ValidaciÃƒÂ³n de autenticaciÃƒÂ³n y permisos (capa HTTP)
        if not request.user.is_authenticated:
            return Response(
                {'error': 'No estÃƒÂ¡s autenticado. Por favor, inicia sesiÃƒÂ³n.'}, 
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
        
        # Extraer timestamp opcional (para sincronizaciÃƒÂ³n offline)
        timestamp_str = request.data.get('timestamp')
        fecha_registro = None
        
        if timestamp_str:
            from django.utils.dateparse import parse_datetime
            fecha_registro = parse_datetime(timestamp_str)
            
            # Validar que el timestamp sea vÃƒÂ¡lido
            if not fecha_registro:
                return Response(
                    {'error': 'Formato de timestamp invÃƒÂ¡lido. Use ISO 8601.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Validar tipo de escaneo
        if tipo_escaneo not in ['ingreso', 'salida']:
            return Response(
                {'error': 'Tipo de escaneo no vÃƒÂ¡lido. Debe ser "ingreso" o "salida".'}, 
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
            
            # Obtener evento si fue especificado
            evento = None
            if evento_id:
                evento = Evento.objects.get(id=evento_id, activo=True)
            
            # Obtener ubicaciÃƒÂ³n si fue especificada
            ubicacion = None
            if ubicacion_id:
                ubicacion = Ubicacion.objects.get(id=ubicacion_id)
            
            # Delegar la lÃƒÂ³gica de negocio al servicio
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
            # Errores de validaciÃƒÂ³n de negocio
            return Response({'error': str(e.message)}, status=status.HTTP_400_BAD_REQUEST)
        
        except BadSignature:
            return Response(
                {'error': 'Alerta de Seguridad: C?digo QR falsificado o inv?lido.'},
                status=status.HTTP_403_FORBIDDEN
            )

        except Usuario.DoesNotExist:
            return Response(
                {'error': 'El DNI escaneado no corresponde a ningÃƒÂºn usuario registrado.'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        except Evento.DoesNotExist:
            return Response(
                {'error': 'El evento seleccionado no es vÃƒÂ¡lido o no estÃƒÂ¡ activo.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        except Ubicacion.DoesNotExist:
            return Response(
                {'error': 'La ubicaciÃƒÂ³n seleccionada no es vÃƒÂ¡lida.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        except base64.binascii.Error:
            return Response(
                {'error': 'El cÃƒÂ³digo QR escaneado es invÃƒÂ¡lido.'}, 
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
    # Por defecto, los asistentes van primero o segÃƒÂºn la lÃƒÂ³gica de reports.py
    
    total_registros = len(unified_list)
    # Mutuamente excluyentes:
    confirmadas = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and (getattr(item, 'hora_salida', None) or item.usuario.estado == 'EXONERADO'))
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    inasistencias = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    pendientes = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and not getattr(item, 'hora_salida', None) and item.usuario.estado != 'EXONERADO')
    
    # LÃƒÂ³gica de Contadores segÃƒÂºn selecciÃƒÂ³n de evento
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
        # Si hay evento, "PadrÃƒÂ³n Esperado" debe ser el total de usuarios empadronados (Activos + Pasivos + Exonerados)
        # independientemente de cuÃƒÂ¡ntos registros traiga el filtrado (unified_list)
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

    context = {
        'page_obj': page_obj,
        'form': form,
        'report_years': report_years,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr'),
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
    Genera un PDF histÃƒÂ³rico de TODO el sistema, agrupado por eventos
    en orden cronolÃƒÂ³gico (Enero a Diciembre).
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
        
        # Procesar rÃƒÂ©cords unificados para este evento
        records = []
        for a in asistencias:
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
            # Buscar justificaciÃƒÂ³n
            just = Justificacion.objects.filter(usuario=u, evento=ev, estado='APROBADO').first()
            records.append({
                'usuario': u,
                'is_absent': True,
                'es_justificada': just is not None,
                'justificacion_obs': just.motivo if just else ""
            })
            
        # EstadÃƒÂ­sticas del evento
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
                descripcion=f"{request.user.username} generÃ³ y descargó una copia de seguridad."
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

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def restaurar_backup(request):
    """
    Restaura el sistema (DB + Media) desde un archivo subido.
    Solo superusuarios.
    """
    if not request.user.is_superuser:
        messages.error(request, "Solo superusuarios pueden restaurar backups.")
        return redirect('dashboard')

    if request.method == 'POST' and request.FILES.get('backup_file'):
        backup_file = request.FILES['backup_file']
        max_upload_size_bytes = 512 * 1024 * 1024  # 512 MB
        max_members = 20000
        max_uncompressed_main_bytes = 2 * 1024 * 1024 * 1024  # 2 GB
        max_uncompressed_media_bytes = 4 * 1024 * 1024 * 1024  # 4 GB

        before = {
            'usuarios': Usuario.objects.count(),
            'asistencias': Asistencia.objects.count(),
            'eventos': Evento.objects.count(),
        }

        if not backup_file.name.endswith('.tar.gz'):
            messages.error(request, "El archivo debe ser un .tar.gz valido.")
            return redirect('dashboard')

        if backup_file.size > max_upload_size_bytes:
            max_mb = max_upload_size_bytes // (1024 * 1024)
            messages.error(request, f"El backup supera el limite de {max_mb} MB.")
            return redirect('dashboard')

        # Directorio temporal para la restauracion
        temp_restore_root = os.path.join(settings.BASE_DIR, 'temp_restore_web')
        if os.path.exists(temp_restore_root):
            shutil.rmtree(temp_restore_root)
        os.makedirs(temp_restore_root)

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

            # Buscar el directorio interno que contiene el respaldo de base de datos
            internal_dirs = [d for d in os.listdir(temp_restore_root) if os.path.isdir(os.path.join(temp_restore_root, d))]
            if not internal_dirs:
                raise Exception("Estructura de backup invalida.")

            extract_path = os.path.join(temp_restore_root, internal_dirs[0])
            sql_file = os.path.join(extract_path, 'database.sql')
            sqlite_file = os.path.join(extract_path, 'database.sqlite3')
            media_tar = os.path.join(extract_path, 'media.tar.gz')

            # 2. Restaurar Base de Datos
            db_conf = settings.DATABASES['default']
            db_engine = db_conf.get('ENGINE', '')

            if db_engine.endswith('sqlite3'):
                if not os.path.exists(sqlite_file):
                    raise Exception("No se encontro database.sqlite3 en el backup.")
                connection.close()
                shutil.copy2(sqlite_file, str(db_conf['NAME']))
            else:
                if not os.path.exists(sql_file):
                    raise Exception("No se encontro database.sql en el backup.")

                env = os.environ.copy()
                env['PGPASSWORD'] = str(db_conf.get('PASSWORD', ''))

                subprocess.run([
                    'psql',
                    '-h', db_conf['HOST'],
                    '-p', str(db_conf['PORT']),
                    '-U', db_conf['USER'],
                    '-d', db_conf['NAME'],
                    '-v', 'ON_ERROR_STOP=1',
                    '--single-transaction',
                    '-f', sql_file
                ], env=env, check=True)

            # 3. Restaurar Media
            if os.path.exists(media_tar):
                media_root = settings.MEDIA_ROOT
                if os.path.exists(media_root):
                    shutil.rmtree(media_root)

                with tarfile.open(media_tar, "r:gz") as mt:
                    validate_tar_limits(
                        mt,
                        max_members=max_members,
                        max_total_uncompressed_size=max_uncompressed_media_bytes,
                        label="Media backup",
                    )
                    # El media_tar contiene 'media/' como top level
                    safe_extract(mt, path=settings.BASE_DIR)

            messages.success(request, "Sistema restaurado exitosamente.")

            LogAccion.objects.create(
                usuario=request.user,
                accion="Restaurar Backup",
                descripcion=f"{request.user.username} restauro el sistema desde {backup_file.name}."
            )
            after = {
                'usuarios': Usuario.objects.count(),
                'asistencias': Asistencia.objects.count(),
                'eventos': Evento.objects.count(),
            }
            log_critical_change(
                request.user,
                'Sistema',
                before,
                after,
                action_label='Restauracion de backup',
            )

        except Exception as e:
            messages.error(request, f"Error en restauracion: {str(e)}")
        finally:
            shutil.rmtree(temp_restore_root)

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
        descripcion=f"{request.user.username} exportÃƒÂ³ las asistencias a Excel."
    )
    return generate_attendance_excel(unified_list)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def exportar_reporte_global_excel(request):
    """
    Genera un Excel histÃƒÂ³rico de TODO el sistema, similar al reporte global PDF.
    """
    from .utils.reports import generate_global_attendance_excel, get_filtered_attendance_data
    
    system_config = ConfiguracionSistema.objects.first()
    tardanza_activa = bool(system_config and system_config.tardanza_activa)

    
    eventos = Evento.objects.all().order_by('fecha')
    report_data = []
    
    for ev in eventos:
        data = get_filtered_attendance_data({'evento': ev})
        asistencias = list(data.get('asistencias', []))
        no_asistentes = data.get('usuarios_no_asistentes', [])
        
        records = []
        for a in asistencias:
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
            just = Justificacion.objects.filter(usuario=u, evento=ev, estado='APROBADO').first()
            records.append({
                'usuario': u,
                'is_absent': True,
                'es_justificada': just is not None,
                'justificacion_obs': just.motivo if just else ""
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
        descripcion=f"{request.user.username} exportÃƒÂ³ el reporte anual consolidado a Excel ({len(eventos)} eventos)."
    )
    
    return generate_global_attendance_excel(report_data, system_config)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def confirmar_asistencia(request, asistencia_id):
    asistencia = get_object_or_404(Asistencia, id=asistencia_id)
    if not asistencia.confirmada:
        asistencia.confirmada = True
        asistencia.save()
        messages.success(request, f"Asistencia de {asistencia.usuario} confirmada exitosamente.")
        LogAccion.objects.create(
            usuario=request.user,
            accion="Confirmar asistencia",
            descripcion=f"{request.user.username} confirmÃƒÂ³ la asistencia de {asistencia.usuario.nombre} {asistencia.usuario.apellido} para el evento {asistencia.evento.nombre if asistencia.evento else 'sin evento'}."
            )
    else:
        messages.warning(request, f"La asistencia de {asistencia.usuario} ya estaba confirmada.")
    return redirect('historial_asistencias')

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_pdf(request):
    form = FiltroAsistenciaForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    data = get_filtered_attendance_data(filters)
    
    # Usar la lista unificada
    unified_list = data.get('unified_report', [])
    
    # Calcular estadÃƒÂ­sticas detalladas
    total_padron = len(unified_list) # Usar el padrÃƒÂ³n del reporte (Activos + Exon)
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

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_usuario_pdf(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    sistema_config = ConfiguracionSistema.objects.first()
    tardanza_activa = bool(sistema_config and sistema_config.tardanza_activa)
    
    # 1. Obtener TODOS los eventos histÃƒÂ³ricos ordenados por fecha descendente
    todos_eventos = Evento.objects.all().order_by('-fecha', '-hora_ingreso')
    
    # 2. Obtener asistencias del usuario mapeadas por ID de evento
    asistencias = Asistencia.objects.filter(usuario=usuario).select_related('evento', 'ubicacion')
    asistencia_map = {a.evento_id: a for a in asistencias}
    
    # 3. Obtener justificaciones APROBADAS mapeadas por ID de evento
    justificaciones = Justificacion.objects.filter(usuario=usuario, estado='APROBADO')
    justificacion_map = {j.evento_id: j for j in justificaciones}
    
    unified_list = []
    
    # 4. Construir la lista unificada recorriendo TODOS los eventos
    for evento in todos_eventos:
        if evento.id in asistencia_map:
            # CASO 1: ASISTIÃƒâ€œ (O PENDIENTE)
            # El usuario tiene un registro de asistencia
            item = asistencia_map[evento.id]
            # Mantener el estado real de justificaciÃƒÂ³n guardado en el registro.
            item.es_justificada = bool(getattr(item, 'es_justificada', False))
            item.is_late_absent = bool(
                tardanza_activa
                and getattr(item, 'puntualidad', None) == Asistencia.PUNTUALIDAD_TARDE
                and item.usuario.estado != Usuario.ESTADO_EXONERADO
            )
            item.is_absent = item.es_justificada or item.is_late_absent
            unified_list.append(item)
            
        elif evento.id in justificacion_map:
            # CASO 2: FALTA JUSTIFICADA
            # No tiene asistencia pero sÃƒÂ­ justificaciÃƒÂ³n aprobada
            justificacion = justificacion_map[evento.id]
            
            # Crear objeto mock para el template
            mock_item = type('MockAsistencia', (object,), {
                'fecha': evento.fecha,
                'hora_ingreso': None,
                'hora_salida': None,
                'evento': evento,
                'ubicacion': None, # No hay ubicaciÃƒÂ³n registrada para la falta
                'usuario': usuario,
                'is_absent': True, # Es ausencia fÃƒÂ­sica
                'es_justificada': True, # Pero justificada
                'justificacion_obs': justificacion.motivo,
                'estado_display': 'JUSTIFICADA'
            })
            unified_list.append(mock_item)
            
        else:
            # CASO 3: FALTA INJUSTIFICADA
            # No hay registro ni justificaciÃƒÂ³n
            mock_item = type('MockAsistencia', (object,), {
                'fecha': evento.fecha,
                'hora_ingreso': None,
                'hora_salida': None,
                'evento': evento,
                'ubicacion': None,
                'usuario': usuario,
                'is_absent': True,
                'es_justificada': False,
                'estado_display': 'FALTA'
            })
            unified_list.append(mock_item)

    # Calcular mÃƒÂ©tricas avanzadas sobre la lista completa
    total_eventos = len(todos_eventos)
    
    # Confirmadas: Tiene salida O es exonerado (y no es mock de falta/justificada)
    # Nota: Los mocks tienen is_absent=True. Los objetos reales Asistencia tienen is_absent=False (seteado arriba) o default False.
    asistencias_confirmadas = sum(1 for item in unified_list 
                                  if not getattr(item, 'is_absent', False) and 
                                  (getattr(item, 'hora_salida', None) or item.usuario.estado == 'EXONERADO'))
                                  
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    
    # Faltas reales: Absent=True y Justificada=False
    faltas = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    
    # Pendientes: No es absent, no tiene salida, no es exonerado
    pendientes = sum(1 for item in unified_list 
                     if not getattr(item, 'is_absent', False) and 
                     not getattr(item, 'hora_salida', None) and 
                     item.usuario.estado != 'EXONERADO')
    
    # Score de asistencia
    total_participaciones = asistencias_confirmadas + justificadas
    score_asistencia = (total_participaciones / total_eventos * 100) if total_eventos > 0 else 0
    
    # Calcular racha de asistencias (eventos consecutivos asistidos/justificados)
    # La lista ya estÃƒÂ¡ ordenada por fecha descendente (mÃƒÂ¡s reciente primero).
    # Para racha actual, contamos desde el inicio hasta que se rompa.
    racha_actual = 0
    for item in unified_list:
        if not getattr(item, 'is_absent', False) or getattr(item, 'es_justificada', False):
             # Consideramos asistencia o justificaciÃƒÂ³n como continuar la racha
             racha_actual += 1
        else:
            if item.fecha < timezone.now().date(): # Si es falta pasada, rompe racha
                break
            # Si es evento futuro (improbable aqui si filtramos por fecha, pero por seguridad), ignorar? 
            # Asumimos que todos_eventos son pasados o presentes.
            break

    # Racha mÃƒÂ¡xima (requiere recorrer cronolÃƒÂ³gicamente o iterar toda la lista)
    racha_maxima = 0
    temp_racha = 0
    # Recorremos en orden CRONOLÃƒâ€œGICO (invertido de unified_list)
    for item in reversed(unified_list):
        if not getattr(item, 'is_absent', False) or getattr(item, 'es_justificada', False):
            temp_racha += 1
            if temp_racha > racha_maxima:
                racha_maxima = temp_racha
        else:
            temp_racha = 0

    # ÃƒÅ¡ltimas 5 asistencias (para el resumen visual, tomamos las 5 primeras del unified que son las recientes)
    ultimas_asistencias = unified_list[:5]
    
    context = {
        'usuario': usuario,
        'unified_list': unified_list,
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64(),
        'sistema_config': ConfiguracionSistema.objects.first(),
        'stats': {
            'total_eventos': total_eventos,
            'asistencias': asistencias_confirmadas,
            'justificadas': justificadas,
            'faltas': faltas,
            'pendientes': pendientes,
            'score': round(score_asistencia, 1),
            'racha_actual': racha_actual,
            'racha_maxima': racha_maxima,
        },
        'ultimas_asistencias': ultimas_asistencias,
    }
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar reporte asistencias usuario PDF",
        descripcion=f"{request.user.username} descargÃƒÂ³ el reporte de asistencias de {usuario.nombre} {usuario.apellido} (DNI: {usuario.dni}) en PDF."
    )
    
    return generate_pdf_report('asistencia/reporte_asistencias_usuario.html', context, f"reporte_asistencias_{usuario.dni}.pdf")

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
        descripcion=f"{request.user.username} descargÃƒÂ³ el reporte del evento {evento.nombre} en PDF."
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
            f"Se cerrÃƒÆ’Ã‚Â³ el evento {evento.nombre}. PDF: {pdf_filename}. "
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
    """Genera un PDF con los filtros aplicados desde la pÃƒÂ¡gina de historial"""
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
    
    # Calcular estadÃƒÂ­sticas
    if effective_filters.get('evento'):
        total_registros = Usuario.objects.filter(
            estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]
        ).count()
    else:
        total_registros = len(unified_list)

    confirmadas = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and (getattr(item, 'hora_salida', None) or item.usuario.estado == 'EXONERADO'))
    pendientes = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and not getattr(item, 'hora_salida', None) and item.usuario.estado != 'EXONERADO')
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
        descripcion=f"{request.user.username} descargÃƒÂ³ un reporte filtrado en PDF."
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
                        errores.append(f"Fila con DNI {dni}: Formato de fecha de nacimiento invÃƒÂ¡lido (use YYYY-MM-DD).")
                        continue

                if 'estado' in row and row['estado']:
                    if row['estado'] not in dict(Usuario.ESTADOS).keys():
                        errores.append(f"Fila con DNI {dni}: Estado invÃƒÂ¡lido. Debe ser ACTIVO, PASIVO o EXONERADO.")
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
                    descripcion=f"{request.user.username} importÃƒÂ³ al usuario {usuario.nombre} {usuario.apellido} (DNI: {usuario.dni})."
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
                descripcion=f"{request.user.username} registrÃƒÂ³ al usuario {usuario.nombre} {usuario.apellido} (DNI: {usuario.dni})."
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
            'error': 'No se encontrÃƒÂ³ un usuario asociado a tu cuenta.',
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
def descargar_todos_carnets_pdf(request):
    """
    Genera un archivo ZIP con:
    - Un PDF de carnets para usuarios con foto.
    - Un PDF de notificaciÃƒÂ³n para usuarios sin foto.

    Para evitar Out Of Memory (OOM), los carnets se procesan en lotes pequeÃƒÂ±os
    y los PDFs parciales se combinan con pypdf.
    """
    import gc
    import time
    from .utils.reports import get_logo_base64

    try:
        from weasyprint import HTML
    except ImportError:
        return HttpResponse("WeasyPrint no estÃƒÂ¡ instalado.", status=500)

    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        return HttpResponse("pypdf no estÃƒÂ¡ instalado. AÃƒÂ±ade 'pypdf' a requirements.txt", status=500)

    started_at = timezone.localtime()
    started_perf = time.perf_counter()
    current_date = started_at.strftime('%d/%m/%Y %H:%M')
    timestamp = started_at.strftime('%Y%m%d_%H%M%S')
    zip_filename = f'carnets_y_pendientes_{timestamp}.zip'

    filtro_sin_foto = Q(foto_perfil__isnull=True) | Q(foto_perfil='')
    base_queryset = Usuario.objects.order_by('apellido', 'nombre').only(
        'id', 'nombre', 'apellido', 'dni', 'estado', 'foto_perfil', 'qr_code'
    )
    usuarios_con_foto_qs = base_queryset.exclude(filtro_sin_foto)
    usuarios_sin_foto_qs = base_queryset.filter(filtro_sin_foto)

    total_con_foto = usuarios_con_foto_qs.count()
    total_sin_foto = usuarios_sin_foto_qs.count()
    total_usuarios = total_con_foto + total_sin_foto
    incidencias = [
        _crear_incidencia_carnet(usuario, 'Falta fotografía.')
        for usuario in usuarios_sin_foto_qs
    ]

    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar carnets ZIP",
        descripcion=(
            f"{request.user.username} iniciÃƒÂ³ la descarga de {total_usuarios} usuarios "
            f"en ZIP: {total_con_foto} con foto y {total_sin_foto} sin foto."
        )
    )

    logo = get_logo_base64()
    sys_config = ConfiguracionSistema.objects.first()
    zip_buffer = io.BytesIO()
    zip_members = []
    total_carnets_generados = 0

    with zipfile.ZipFile(zip_buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zip_file:
        if total_con_foto:
            # Procesar en lotes pequeÃƒÂ±os para evitar OOM.
            # Cada lote se renderiza con WeasyPrint y se libera de memoria inmediatamente.
            CHUNK_SIZE = 10
            writer = PdfWriter()

            for batch_number, chunk_users in enumerate(
                _iter_queryset_in_chunks(usuarios_con_foto_qs, CHUNK_SIZE),
                start=1,
            ):
                usuarios_data = []
                usuarios_validos_chunk = []
                for u in chunk_users:
                    carnet_payload, incidencia = _build_carnet_payload(u)
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
                    'sistema_config': sys_config
                }

                try:
                    html_string = render_to_string('asistencia/reporte_todos_carnets.html', context)
                    pdf_bytes = HTML(string=html_string).write_pdf()

                    reader = PdfReader(io.BytesIO(pdf_bytes))
                    for page in reader.pages:
                        writer.add_page(page)
                    total_carnets_generados += len(usuarios_data)
                except Exception as exc:
                    logger.exception("Error al generar el lote %s de carnets", batch_number)
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

                processed = min(batch_number * CHUNK_SIZE, total_con_foto)
                logger.info(
                    "Carnets progreso: %s/%s candidatos con foto procesados",
                    processed,
                    total_con_foto,
                )

            if total_carnets_generados:
                output_buffer = io.BytesIO()
                writer.write(output_buffer)
                final_pdf = output_buffer.getvalue()
                output_buffer.close()

                zip_file.writestr('carnets_generados.pdf', final_pdf)
                zip_members.append('carnets_generados.pdf')

                del final_pdf

            del writer
            gc.collect()

        if incidencias:
            context_sin_foto = {
                'usuarios': incidencias,
                'logo_base64': logo,
                'current_date': current_date,
                'sistema_config': sys_config,
            }
            html_sin_foto = render_to_string(
                'asistencia/reporte_sin_foto.html',
                context_sin_foto
            )
            pdf_sin_foto = HTML(string=html_sin_foto).write_pdf()
            zip_file.writestr('usuarios_sin_foto_notificar.pdf', pdf_sin_foto)
            zip_members.append('usuarios_sin_foto_notificar.pdf')

            del html_sin_foto, pdf_sin_foto, context_sin_foto
            gc.collect()

    zip_bytes = zip_buffer.getvalue()
    zip_buffer.close()
    duration_seconds = time.perf_counter() - started_perf

    LogAccion.objects.create(
        usuario=request.user,
        accion="Descarga carnets ZIP exitosa",
        descripcion=(
            f"{request.user.username} finalizÃƒÂ³ la descarga ZIP con "
            f"{total_carnets_generados} carnets generados, "
            f"{len(incidencias)} incidencias, archivos {', '.join(zip_members) or 'ninguno'} "
            f"en {duration_seconds:.2f}s."
        )
    )

    response = HttpResponse(zip_bytes, content_type='application/zip')
    response['Content-Disposition'] = (
        f'attachment; filename="{zip_filename}"; '
        f"filename*=UTF-8''{quote(zip_filename)}"
    )
    response['Content-Length'] = len(zip_bytes)
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
