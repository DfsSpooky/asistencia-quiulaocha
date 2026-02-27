from django.db import models
from django.shortcuts import render, redirect, get_object_or_404, redirect
from django.contrib.auth import logout as auth_logout, authenticate, login as auth_login
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import cache_page
from django.views import View
from django.db.models import Q, Count
from django.db import transaction
import csv
import openpyxl # Changed from `from openpyxl import Workbook` to `import openpyxl` for consistency with other imports
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from io import BytesIO
from .models import Usuario, Asistencia, Ubicacion, Evento, LogAccion, ConfiguracionSistema, Justificacion
from .forms import FiltroAsistenciaForm, ImportarUsuariosForm, BuscarUsuarioForm, UsuarioRegistroForm, JustificacionForm, AdminJustificacionForm
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import permission_classes, api_view
from django.utils import timezone
from django.utils.html import escape
from datetime import date, datetime, timedelta
import base64
from .utils.reports import (
    generate_attendance_csv, generate_attendance_excel, 
    generate_pdf_report, get_logo_base64, get_filtered_attendance_data
)
from django.core.management import call_command
import os
import subprocess
import shutil
import tarfile

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

# @require_POST  <-- Comentado para compatibilidad con Jazzmin/Admin GET links
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
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    })

@login_required
@permission_required('asistencia.can_scan_qr', raise_exception=True)
def keep_alive(request):
    """
    Vista para mantener viva la sesión del usuario mientras está en la página de escaneo.
    """
    return JsonResponse({'status': 'success', 'message': 'Sesión mantenida activa'})

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
            justificacion.estado = Justificacion.ESTADO_APROBADO
            justificacion.procesado_por = request.user
            justificacion.save()
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

@method_decorator(csrf_exempt, name='dispatch')
@permission_classes([IsAuthenticated])
class RegistrarAsistencia(APIView):
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
            # Decodificar DNI y obtener usuario
            dni = base64.b64decode(encoded_dni).decode()
            usuario = Usuario.objects.get(dni=dni)
            
            # Obtener evento si fue especificado
            evento = None
            if evento_id:
                evento = Evento.objects.get(id=evento_id, activo=True)
            
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
                'foto_perfil': request.build_absolute_uri(usuario.foto_perfil.url) if usuario.foto_perfil else None
            }, status=status.HTTP_201_CREATED)
            
        except ValidationError as e:
            # Errores de validación de negocio
            return Response({'error': str(e.message)}, status=status.HTTP_400_BAD_REQUEST)
        
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
        
        except Exception as e:
            # Manejar cualquier error inesperado
            return Response(
                {'error': f'Error inesperado: {str(e)}'}, 
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
            'foto_perfil': request.build_absolute_uri(a.usuario.foto_perfil.url) if a.usuario.foto_perfil else None
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
    # Combinamos asistencias y no asistentes en una lista unificada para la tabla
    asistencias = list(data.get('asistencias', []))
    no_asistentes = data.get('usuarios_no_asistentes')
    
    if no_asistentes:
        # Convertimos los usuarios no asistentes en objetos similares a Asistencia
        # para que la tabla pueda iterar uniformemente
        for u in no_asistentes:
            u.is_absent = True
            # Intentar buscar si tiene una justificación
            u.es_justificada = Justificacion.objects.filter(
                usuario=u, 
                evento=filters.get('evento'),
                estado='APROBADO'
            ).exists()
            asistencias.append(u)

    # Si no hay ordenamiento específico de SQLAlchemy, ordenamos la lista resultante
    # (Esto es necesario si mezclamos QuerySets de diferentes tipos)
    unified_list = asistencias
    
    # Re-ordenar la lista unificada si es necesario (ya que mezclamos tipos de objetos)
    # Por defecto, los asistentes van primero o según la lógica de reports.py
    
    total_registros = len(unified_list)
    # Mutuamente excluyentes:
    confirmadas = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and (getattr(item, 'hora_salida', None) or item.usuario.estado == 'EXONERADO'))
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    inasistencias = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    pendientes = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and not getattr(item, 'hora_salida', None) and item.usuario.estado != 'EXONERADO')
    
    # Lógica de Contadores según selección de evento
    if not filters.get('evento'):
        # Si no hay evento seleccionado, mostrar todo en CERO
        total_registros = 0
        confirmadas = 0
        justificadas = 0
        inasistencias = 0
        pendientes = 0
    else:
        # Si hay evento, "Padrón Esperado" debe ser el total de usuarios empadronados (Activos + Pasivos + Exonerados)
        # independientemente de cuántos registros traiga el filtrado (unified_list)
        total_registros = Usuario.objects.filter(estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]).count()
    
    paginator = Paginator(unified_list, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'form': form,
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
def descargar_reporte_global_pdf(request):
    """
    Genera un PDF histórico de TODO el sistema, agrupado por eventos
    en orden cronológico (Enero a Diciembre).
    """
    from .utils.reports import get_logo_base64
    
    # Obtener todos los eventos ordenados por fecha ascendente
    eventos = Evento.objects.all().order_by('fecha')
    
    report_data = []
    total_general_asistencias = 0
    
    for ev in eventos:
        # Para cada evento, obtenemos sus datos usando la utilidad
        data = get_filtered_attendance_data({'evento': ev})
        asistencias = list(data.get('asistencias', []))
        no_asistentes = data.get('usuarios_no_asistentes', [])
        
        # Procesar récords unificados para este evento
        records = []
        for a in asistencias:
            records.append({
                'usuario': a.usuario,
                'hora_ingreso': a.hora_ingreso,
                'hora_salida': a.hora_salida,
                'is_absent': False,
                'es_justificada': a.es_justificada,
            })
            if a.confirmada:
                total_general_asistencias += 1
                
        for u in no_asistentes:
            # Buscar justificación
            just = Justificacion.objects.filter(usuario=u, evento=ev, estado='APROBADO').first()
            records.append({
                'usuario': u,
                'is_absent': True,
                'es_justificada': just is not None,
                'justificacion_obs': just.motivo if just else ""
            })
            
        # Estadísticas del evento
        total_padrón = len(records)
        asistencias_fisicas = sum(1 for r in records if not r['is_absent'] and not r.get('es_justificada', False))
        justificadas = sum(1 for r in records if r.get('es_justificada', False))
        faltas_reales = sum(1 for r in records if r['is_absent'] and not r.get('es_justificada', False))
        presentes_totales = asistencias_fisicas + justificadas
        porcentaje = (presentes_totales / total_padrón * 100) if total_padrón > 0 else 0
        
        report_data.append({
            'evento': ev,
            'records': records,
            'stats': {
                'asistencias_fisicas': asistencias_fisicas,
                'justificadas': justificadas,
                'inasistencias': faltas_reales,
                'porcentaje': porcentaje,
                'total_usuarios': total_padrón
            }
        })
        
    context = {
        'report_data': report_data,
        'logo_base64': get_logo_base64(),
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'sistema_config': ConfiguracionSistema.objects.first(),
        'total_eventos': len(eventos),
    }
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar Reporte Global PDF",
        descripcion=f"{request.user.username} generó el reporte anual consolidado de {len(eventos)} eventos."
    )
    
    return generate_pdf_report('asistencia/reporte_global_anual.html', context, f"reporte_global_{datetime.now().year}.pdf")

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
        # El comando 'backup_db' retorna la ruta absoluta del archivo generado
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        # Capturamos el path del archivo desde el comando (modificado para retornar path)
        # Nota: He modificado el comando en el paso anterior para que retorne el path.
        from asistencia.management.commands.backup_db import Command as BackupCommand
        cmd = BackupCommand()
        file_path = cmd.handle()
        
        if file_path and os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                response = HttpResponse(f.read(), content_type="application/x-gzip")
                response['Content-Disposition'] = f'attachment; filename="{os.path.basename(file_path)}"'
                
                # Opcional: registrar acción
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
    for member in tar.getmembers():
        member_path = os.path.join(path, member.name)
        if os.path.commonpath([os.path.abspath(member_path), os.path.abspath(path)]) != os.path.abspath(path):
            raise Exception("Intento de Path Traversal detectado en el archivo de backup.")
    tar.extractall(path, members, numeric_owner=numeric_owner)

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
        
        if not backup_file.name.endswith('.tar.gz'):
            messages.error(request, "El archivo debe ser un .tar.gz válido.")
            return redirect('dashboard')

        # Directorio temporal para la restauración
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
                safe_extract(tar, path=temp_restore_root)
            
            # Buscar el directorio interno que contiene database.sql
            # El tar.gz suele tener una estructura: backup_name/database.sql
            internal_dirs = [d for d in os.listdir(temp_restore_root) if os.path.isdir(os.path.join(temp_restore_root, d))]
            if not internal_dirs:
                raise Exception("Estructura de backup inválida.")
            
            extract_path = os.path.join(temp_restore_root, internal_dirs[0])
            sql_file = os.path.join(extract_path, 'database.sql')
            media_tar = os.path.join(extract_path, 'media.tar.gz')

            if not os.path.exists(sql_file):
                raise Exception("No se encontró database.sql en el backup.")

            # 2. Restaurar Base de Datos (usando psql directamente)
            db_conf = settings.DATABASES['default']
            env = os.environ.copy()
            env['PGPASSWORD'] = db_conf['PASSWORD']
            
            # Nota: El psql restaurará sobre la DB actual.
            # No podemos 'dropear' la DB mientras estamos conectados, pero podemos sobreescribir esquemas 
            # o simplemente importar. El pg_dump del comando anterior no usa --clean, 
            # así que lo mejor es importar.
            
            subprocess.run([
                'psql',
                '-h', db_conf['HOST'],
                '-p', str(db_conf['PORT']),
                '-U', db_conf['USER'],
                '-d', db_conf['NAME'],
                '-f', sql_file
            ], env=env, check=True)

            # 3. Restaurar Media
            if os.path.exists(media_tar):
                media_root = settings.MEDIA_ROOT
                if os.path.exists(media_root):
                    shutil.rmtree(media_root)
                
                with tarfile.open(media_tar, "r:gz") as mt:
                    # El media_tar contiene 'media/' como top level
                    safe_extract(mt, path=settings.BASE_DIR)

            messages.success(request, "¡Sistema restaurado exitosamente!")
            
            LogAccion.objects.create(
                usuario=request.user,
                accion="Restaurar Backup",
                descripcion=f"{request.user.username} restauró el sistema desde {backup_file.name}."
            )

        except Exception as e:
            messages.error(request, f"Error en restauración: {str(e)}")
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
    
    eventos = Evento.objects.all().order_by('fecha')
    report_data = []
    
    for ev in eventos:
        data = get_filtered_attendance_data({'evento': ev})
        asistencias = list(data.get('asistencias', []))
        no_asistentes = data.get('usuarios_no_asistentes', [])
        
        records = []
        for a in asistencias:
            records.append({
                'usuario': a.usuario,
                'hora_ingreso': a.hora_ingreso,
                'hora_salida': a.hora_salida,
                'is_absent': False,
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
            
        total_padrón = len(records)
        asistencias_fisicas = sum(1 for r in records if not r['is_absent'] and not r.get('es_justificada', False))
        justificadas = sum(1 for r in records if r.get('es_justificada', False))
        faltas_reales = sum(1 for r in records if r['is_absent'] and not r.get('es_justificada', False))
        presentes_totales = asistencias_fisicas + justificadas
        porcentaje = (presentes_totales / total_padrón * 100) if total_padrón > 0 else 0
        
        report_data.append({
            'evento': ev,
            'records': records,
            'stats': {
                'asistencias_fisicas': asistencias_fisicas,
                'justificadas': justificadas,
                'inasistencias': faltas_reales,
                'total_usuarios': total_padrón,
                'porcentaje': porcentaje,
            }
        })
        
    system_config = ConfiguracionSistema.objects.first()
    
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
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_pdf(request):
    form = FiltroAsistenciaForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    data = get_filtered_attendance_data(filters)
    
    # Usar la lista unificada
    unified_list = data.get('unified_report', [])
    
    # Calcular estadísticas detalladas
    total_padrón = len(unified_list) # Usar el padrón del reporte (Activos + Exon)
    asistencias_puras = sum(1 for item in unified_list if not getattr(item, 'is_absent', False))
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    total_asistentes_efectivos = asistencias_puras + justificadas
    total_inasistentes_reales = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    
    porcentaje_asistencia = (total_asistentes_efectivos / total_padrón * 100) if total_padrón > 0 else 0

    context = {
        'unified_list': unified_list,
        'total_usuarios': total_padrón,
        'total_asistentes': total_asistentes_efectivos,
        'asistencias_puras': asistencias_puras,
        'justificadas': justificadas,
        'total_inasistentes': total_inasistentes_reales,
        'porcentaje_asistencia': porcentaje_asistencia,
        'logo_base64': get_logo_base64(),
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'filtros': {
            'fecha_inicio': filters.get('fecha_inicio').strftime('%d/%m/%Y') if filters.get('fecha_inicio') else 'Inicio',
            'fecha_fin': filters.get('fecha_fin').strftime('%d/%m/%Y') if filters.get('fecha_fin') else 'Hoy',
            'evento': filters.get('evento').nombre if filters.get('evento') else 'Todos',
            'dni': filters.get('dni') or 'Todos'
        }
    }
    
    return generate_pdf_report('asistencia/reporte_asistencias.html', context, "reporte_asistencias.pdf")

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_usuario_pdf(request, dni):
    usuario = get_object_or_404(Usuario, dni=dni)
    
    # 1. Obtener TODOS los eventos históricos ordenados por fecha descendente
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
            # CASO 1: ASISTIÓ (O PENDIENTE)
            # El usuario tiene un registro de asistencia
            item = asistencia_map[evento.id]
            # Aseguramos que los atributos helpers existan (aunque sea el objeto ORM)
            item.is_absent = False
            item.es_justificada = False
            unified_list.append(item)
            
        elif evento.id in justificacion_map:
            # CASO 2: FALTA JUSTIFICADA
            # No tiene asistencia pero sí justificación aprobada
            justificacion = justificacion_map[evento.id]
            
            # Crear objeto mock para el template
            mock_item = type('MockAsistencia', (object,), {
                'fecha': evento.fecha,
                'hora_ingreso': None,
                'hora_salida': None,
                'evento': evento,
                'ubicacion': None, # No hay ubicación registrada para la falta
                'usuario': usuario,
                'is_absent': True, # Es ausencia física
                'es_justificada': True, # Pero justificada
                'justificacion_obs': justificacion.motivo,
                'estado_display': 'JUSTIFICADA'
            })
            unified_list.append(mock_item)
            
        else:
            # CASO 3: FALTA INJUSTIFICADA
            # No hay registro ni justificación
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

    # Calcular métricas avanzadas sobre la lista completa
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
    # La lista ya está ordenada por fecha descendente (más reciente primero).
    # Para racha actual, contamos desde el inicio hasta que se rompa.
    racha_actual = 0
    for item in unified_list:
        if not getattr(item, 'is_absent', False) or getattr(item, 'es_justificada', False):
             # Consideramos asistencia o justificación como continuar la racha
             racha_actual += 1
        else:
            if item.fecha < timezone.now().date(): # Si es falta pasada, rompe racha
                break
            # Si es evento futuro (improbable aqui si filtramos por fecha, pero por seguridad), ignorar? 
            # Asumimos que todos_eventos son pasados o presentes.
            break

    # Racha máxima (requiere recorrer cronológicamente o iterar toda la lista)
    racha_maxima = 0
    temp_racha = 0
    # Recorremos en orden CRONOLÓGICO (invertido de unified_list)
    for item in reversed(unified_list):
        if not getattr(item, 'is_absent', False) or getattr(item, 'es_justificada', False):
            temp_racha += 1
            if temp_racha > racha_maxima:
                racha_maxima = temp_racha
        else:
            temp_racha = 0

    # Últimas 5 asistencias (para el resumen visual, tomamos las 5 primeras del unified que son las recientes)
    ultimas_asistencias = unified_list[:5]
    
    context = {
        'usuario': usuario,
        'unified_list': unified_list,
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64(),
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
        descripcion=f"{request.user.username} descargó el reporte de asistencias de {usuario.nombre} {usuario.apellido} (DNI: {usuario.dni}) en PDF."
    )
    
    return generate_pdf_report('asistencia/reporte_asistencias_usuario.html', context, f"reporte_asistencias_{usuario.dni}.pdf")

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def descargar_reporte_evento_pdf(request, evento_id):
    evento = get_object_or_404(Evento, id=evento_id)
    form = FiltroAsistenciaForm(request.GET or None)
    
    filters = form.cleaned_data if form.is_valid() else {}
    filters['evento'] = evento  # Forzar el evento
    
    data = get_filtered_attendance_data(filters)
    unified_list = data.get('unified_report', [])
    
    # Calcular estadísticas: Consideramos Justificadas como "Asistencia Efectiva"
    total_padrón = Usuario.objects.filter(estado__in=[Usuario.ESTADO_ACTIVO, Usuario.ESTADO_EXONERADO, Usuario.ESTADO_PASIVO]).count()
    
    # Asistieron físicamente y confirmados (tienen salida O son exonerados)
    asistencias_fisicas = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and (getattr(item, 'hora_salida', None) or item.usuario.estado == 'EXONERADO'))
    # Justificaron (con permiso aprobado)
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    # Inasistencias reales (ni fueron ni justificaron)
    inasistencias_reales = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    # Pendientes de confirmación física
    pendientes = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and not getattr(item, 'hora_salida', None) and item.usuario.estado != 'EXONERADO')
    
    total_asistentes_efectivos = asistencias_fisicas + justificadas
    porcentaje_asistencia = (total_asistentes_efectivos / total_padrón * 100) if total_padrón > 0 else 0
    
    context = {
        'evento': evento,
        'unified_list': unified_list,
        'total_usuarios': total_padrón,
        'total_asistentes': asistencias_fisicas, # Solo fìsicos confirmados
        'justificadas': justificadas,
        'total_inasistentes': inasistencias_reales,
        'pendientes': pendientes,
        'porcentaje_asistencia': porcentaje_asistencia,
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64(),
        'filtros': {
            'dni': filters.get('dni') or 'Todos',
            'fecha_inicio': filters.get('fecha_inicio').strftime('%d/%m/%Y') if filters.get('fecha_inicio') else None,
            'fecha_fin': filters.get('fecha_fin').strftime('%d/%m/%Y') if filters.get('fecha_fin') else None,
            'evento': evento.nombre
        }
    }
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar reporte evento PDF",
        descripcion=f"{request.user.username} descargó el reporte del evento {evento.nombre} en PDF."
    )
    
    return generate_pdf_report('asistencia/reporte_evento.html', context, f"reporte_evento_{evento.id}.pdf")

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
    data = get_filtered_attendance_data(filters)
    unified_list = data.get('unified_report', [])
    
    # Calcular estadísticas
    total_registros = len(unified_list)
    confirmadas = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and (getattr(item, 'hora_salida', None) or item.usuario.estado == 'EXONERADO'))
    pendientes = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and not getattr(item, 'hora_salida', None) and item.usuario.estado != 'EXONERADO')
    inasistencias = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    justificadas = sum(1 for item in unified_list if getattr(item, 'es_justificada', False))
    
    context = {
        'unified_list': unified_list,
        'total_registros': total_registros,
        'confirmadas': confirmadas,
        'pendientes': pendientes,
        'inasistencias': inasistencias,
        'justificadas': justificadas,
        'current_date': timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64(),
        'filtros': {
            'dni': filters.get('dni') or 'Todos',
            'fecha_inicio': filters.get('fecha_inicio').strftime('%d/%m/%Y') if filters.get('fecha_inicio') else 'Sin filtro',
            'fecha_fin': filters.get('fecha_fin').strftime('%d/%m/%Y') if filters.get('fecha_fin') else 'Sin filtro',
            'evento': filters.get('evento').nombre if filters.get('evento') else 'Todos los eventos',
            'ubicacion': filters.get('ubicacion').nombre if filters.get('ubicacion') else 'Todas',
            'estado': dict(form.fields['estado'].choices).get(filters.get('estado', ''), 'Todos') if filters.get('estado') else 'Todos',
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
        context = {
            'usuario': usuario,
            'asistencias': asistencias,
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
def descargar_todos_carnets_pdf(request):
    """
    Genera un PDF con todos los carnets usando el template HTML original.
    Para evitar Out Of Memory (OOM), procesa los usuarios en lotes pequeños
    y combina los PDFs parciales con pypdf.
    """
    import io
    import gc
    from .utils.reports import get_image_base64, get_logo_base64
    from django.template.loader import render_to_string
    from django.http import HttpResponse

    try:
        from weasyprint import HTML
    except ImportError:
        return HttpResponse("WeasyPrint no está instalado.", status=500)

    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        return HttpResponse("pypdf no está instalado. Añade 'pypdf' a requirements.txt", status=500)

    # Obtener todos los usuarios ordenados
    usuarios = list(Usuario.objects.all().order_by('apellido', 'nombre'))

    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar todos los carnets PDF",
        descripcion=f"{request.user.username} inició la descarga de {len(usuarios)} carnets en PDF."
    )

    logo = get_logo_base64()
    sys_config = ConfiguracionSistema.objects.first()

    # Procesar en lotes pequeños para evitar OOM
    # Cada lote se renderiza con WeasyPrint y se libera de memoria inmediatamente
    CHUNK_SIZE = 10
    writer = PdfWriter()

    for i in range(0, len(usuarios), CHUNK_SIZE):
        chunk_users = usuarios[i:i + CHUNK_SIZE]
        usuarios_data = []
        for u in chunk_users:
            usuarios_data.append({
                'nombre': u.nombre,
                'apellido': u.apellido,
                'dni': u.dni,
                'estado': u.get_estado_display(),
                'id': u.id,
                'foto_base64': get_image_base64(u.foto_perfil),
                'qr_base64': get_image_base64(u.qr_code)
            })

        context = {
            'usuarios': usuarios_data,
            'logo_base64': logo,
            'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
            'total_usuarios': len(usuarios_data),
            'sistema_config': sys_config
        }

        # Renderizar el HTML con el template original (diseño bonito)
        html_string = render_to_string('asistencia/reporte_todos_carnets.html', context)

        # Generar PDF del lote con WeasyPrint
        pdf_bytes = HTML(string=html_string).write_pdf()

        # Agregar páginas al escritor final usando pypdf
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)

        # Liberar memoria inmediatamente
        del pdf_bytes, reader, html_string, usuarios_data, context
        gc.collect()

        # Log de progreso
        processed = min(i + CHUNK_SIZE, len(usuarios))
        print(f"Carnets progreso: {processed}/{len(usuarios)} procesados...")

    # Escribir el PDF final combinado
    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    final_pdf = output_buffer.getvalue()
    output_buffer.close()

    LogAccion.objects.create(
        usuario=request.user,
        accion="Descarga todos los carnets exitosa",
        descripcion=f"{request.user.username} finalizó la descarga de {len(usuarios)} carnets en PDF."
    )

    response = HttpResponse(final_pdf, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="todos_los_carnets.pdf"'
    response['Content-Length'] = len(final_pdf)
    return response
