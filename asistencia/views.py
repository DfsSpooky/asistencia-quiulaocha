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
from datetime import date, datetime, timedelta
import base64
from .utils.reports import (
    generate_attendance_csv, generate_attendance_excel, 
    generate_pdf_report, get_logo_base64, get_filtered_attendance_data
)

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
    total_usuarios = Usuario.objects.count()
    asistencias_hoy = Asistencia.objects.filter(fecha=date.today()).count()
    eventos_activos = Evento.objects.filter(activo=True).count()
    
    context = {
        'total_usuarios': total_usuarios,
        'asistencias_hoy': asistencias_hoy,
        'eventos_activos': eventos_activos,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    }
    return render(request, 'asistencia/dashboard.html', context)

class CustomLoginView(LoginView):
    template_name = 'asistencia/login.html'

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
    ubicaciones = Ubicacion.objects.all()
    eventos = Evento.objects.filter(activo=True).order_by('-fecha')
    context = {
        'ubicaciones': ubicaciones,
        'eventos': eventos,
        'selected_evento_id': evento_id,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    }
    return render(request, 'asistencia/escanear.html', context)

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
            
            # Retornar respuesta exitosa
            return Response({
                'message': message,
                'hora': hora.strftime('%H:%M:%S') if hora else 'No registrado',
                'nombre': f"{usuario.nombre} {usuario.apellido}",
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
    confirmadas = sum(1 for item in unified_list if getattr(item, 'confirmada', False))
    inasistencias = sum(1 for item in unified_list if getattr(item, 'is_absent', False))
    pendientes = sum(1 for item in unified_list if not getattr(item, 'is_absent', False) and not getattr(item, 'confirmada', False))
    
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
            'pendientes': pendientes,
            'inasistencias': inasistencias,
        },
    }
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
        presentes = sum(1 for r in records if not r['is_absent'] or r['es_justificada'])
        faltas_reales = sum(1 for r in records if r['is_absent'] and not r['es_justificada'])
        porcentaje = (presentes / total_padrón * 100) if total_padrón > 0 else 0
        
        report_data.append({
            'evento': ev,
            'records': records,
            'stats': {
                'confirmadas': presentes, # Incluye justificadas
                'inasistencias': faltas_reales,
                'porcentaje': porcentaje,
                'total_usuarios': total_padrón
            }
        })
        
    context = {
        'report_data': report_data,
        'logo_base64': get_logo_base64(),
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
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
    
    # Calcular estadísticas en memoria
    total_usuarios = Usuario.objects.count()
    total_asistentes = sum(1 for item in unified_list if not getattr(item, 'is_absent', False))
    total_inasistentes = sum(1 for item in unified_list if getattr(item, 'is_absent', False))
    
    # Para el porcentaje, usamos el total de usuarios activos vs asistentes en el reporte
    # OJO: Si hay filtros, el porcentaje es relativo al filtro? 
    # Generalmente se desea Asistencia Global.
    # Mantenemos lógica simple: Asistentes / Total Usuarios * 100
    porcentaje_asistencia = (total_asistentes / total_usuarios * 100) if total_usuarios > 0 else 0

    context = {
        'unified_list': unified_list, # Nueva clave para el template
        'total_usuarios': total_usuarios,
        'total_asistentes': total_asistentes,
        'total_inasistentes': total_inasistentes,
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
    
    # Usar el utility pasando el filtro por usuario (vía DNI en este caso o ajustando el utility)
    # El utility actual filtra por usuario__dni__icontains=dni si se pasa dni en el dict.
    data = get_filtered_attendance_data({'dni': dni})
    asistencias = data['asistencias']
    
    context = {
        'usuario': usuario,
        'asistencias': asistencias,
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64()
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
    total_usuarios = Usuario.objects.filter(estado=Usuario.ESTADO_ACTIVO).count()
    # Asistieron fìsicamente
    asistencias_puras = sum(1 for item in unified_list if not getattr(item, 'is_absent', False))
    # Justificaron (no fueron pero tienen permiso)
    justificadas = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and getattr(item, 'es_justificada', False))
    
    total_asistentes_efectivos = asistencias_puras + justificadas
    total_inasistentes_reales = sum(1 for item in unified_list if getattr(item, 'is_absent', False) and not getattr(item, 'es_justificada', False))
    
    porcentaje_asistencia = (total_asistentes_efectivos / total_usuarios * 100) if total_usuarios > 0 else 0
    
    context = {
        'evento': evento,
        'unified_list': unified_list,
        'total_usuarios': total_usuarios,
        'total_asistentes': total_asistentes_efectivos, # Incluye justificadas
        'total_inasistentes': total_inasistentes_reales,
        'porcentaje_asistencia': porcentaje_asistencia,
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
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
            return HttpResponse(f'<div class="mt-2 text-[10px] font-black text-emerald-600 animate-pulse uppercase tracking-widest"><i class="bi bi-check-circle-fill"></i> Socio: {usuario.nombre} {usuario.apellido}</div>')
        else:
            return HttpResponse('<div class="mt-2 text-[10px] font-black text-red-500 uppercase tracking-widest"><i class="bi bi-x-circle-fill"></i> Socio no encontrado</div>')
    return HttpResponse('')

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
    from .utils.reports import get_image_base64
    
    # Obtener todos los usuarios ordenados por apellidos y nombres
    usuarios = Usuario.objects.all().order_by('apellido', 'nombre')
    
    # Pre-procesar usuarios con sus imágenes en base64 para el PDF
    usuarios_data = []
    for u in usuarios:
        usuarios_data.append({
            'nombre': u.nombre,
            'apellido': u.apellido,
            'dni': u.dni,
            'estado': u.get_estado_display(),
            'id': u.id,
            'foto_base64': get_image_base64(u.foto_perfil),
            'qr_base64': get_image_base64(u.qr_code)
        })
    
    # Los pasamos en una lista plana, el template se encargará de los saltos de página
    context = {
        'usuarios': usuarios_data,
        'logo_base64': get_logo_base64(),
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'total_usuarios': len(usuarios_data),
        'sistema_config': ConfiguracionSistema.objects.first()
    }
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Descargar todos los carnets PDF",
        descripcion=f"{request.user.username} descargó los carnets de todos los usuarios ({len(usuarios_data)}) en PDF."
    )
    
    return generate_pdf_report('asistencia/reporte_todos_carnets.html', context, "todos_los_carnets.pdf")