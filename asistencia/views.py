from django.db import models
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import cache_page
from .models import Usuario, Asistencia, Ubicacion, Evento, LogAccion, ConfiguracionSistema, Justificacion
from .forms import FiltroAsistenciaForm, ImportarUsuariosForm, BuscarUsuarioForm, UsuarioRegistroForm, JustificacionForm, AdminJustificacionForm
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import permission_classes
from datetime import date, datetime, timedelta
import base64
from .utils.reports import (
    generate_attendance_csv, generate_attendance_excel, 
    generate_pdf_report, get_logo_base64, get_filtered_attendance_data
)

def index(request):
    if not request.user.is_authenticated:
        return redirect('login')
    if request.user.is_staff:
        return redirect('dashboard')
    return redirect('perfil_usuario')

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

@require_POST
@csrf_protect
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
    
    return render(request, 'asistencia/admin_solicitar_justificacion.html', {
        'form': form,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr')
    })

@method_decorator(csrf_exempt, name='dispatch')
@permission_classes([IsAuthenticated])
class RegistrarAsistencia(APIView):
    def post(self, request):
        print("Usuario autenticado:", request.user)
        print("¿Está autenticado?:", request.user.is_authenticated)
        if not request.user.is_authenticated:
            print("Error: El usuario no está autenticado.")
            return Response({'error': 'No estás autenticado. Por favor, inicia sesión.'}, status=status.HTTP_403_FORBIDDEN)
        
        if not request.user.has_perm('asistencia.can_scan_qr'):
            print(f"Error: El usuario {request.user.username} no tiene permiso para escanear.")
            return Response({'error': 'No tienes permiso para registrar asistencias.'}, status=status.HTTP_403_FORBIDDEN)
        
        print("Solicitud POST recibida:", request.data)
        encoded_dni = request.data.get('dni')
        ubicacion_id = request.data.get('ubicacion_id')
        evento_id = request.data.get('evento_id')
        tipo_escaneo = request.data.get('tipo_escaneo')
        try:
            dni = base64.b64decode(encoded_dni).decode()
            print("DNI decodificado:", dni)
            usuario = Usuario.objects.get(dni=dni)
            if usuario.estado not in [Usuario.ESTADO_ACTIVO, Usuario.ESTADO_PASIVO]:
                return Response({
                    'error': f'El usuario {usuario.nombre} {usuario.apellido} no tiene permiso (Estado: {usuario.get_estado_display()}). Solo Activos y Pasivos pueden marcar.'
                }, status=status.HTTP_400_BAD_REQUEST)
            today = date.today()
            evento = Evento.objects.get(id=evento_id, activo=True) if evento_id else None
            if evento and evento.fecha != today:
                return Response({
                    'error': f'El evento {evento.nombre} no está programado para hoy.'
                }, status=status.HTTP_400_BAD_REQUEST)

            existing_asistencia = Asistencia.objects.filter(
                usuario=usuario,
                evento=evento,
                fecha=today
            ).first()

            current_time = datetime.now().time()

            if tipo_escaneo == 'ingreso':
                if existing_asistencia and existing_asistencia.hora_ingreso:
                    return Response({
                        'error': f'{usuario.nombre} {usuario.apellido} ya registró su ingreso para el evento {evento.nombre} hoy a las {existing_asistencia.hora_ingreso}.'
                    }, status=status.HTTP_400_BAD_REQUEST)
                if existing_asistencia:
                    existing_asistencia.hora_ingreso = current_time
                    existing_asistencia.save()
                    asistencia = existing_asistencia
                else:
                    ubicacion = Ubicacion.objects.get(id=ubicacion_id) if ubicacion_id else None
                    asistencia = Asistencia.objects.create(
                        usuario=usuario,
                        hora_ingreso=current_time,
                        ubicacion=ubicacion,
                        evento=evento
                    )
                message = f'Ingreso registrado para {usuario.nombre} {usuario.apellido} en el evento {evento.nombre if evento else "sin evento"} con éxito.'
                hora = asistencia.hora_ingreso
            elif tipo_escaneo == 'salida':
                if not existing_asistencia or not existing_asistencia.hora_ingreso:
                    return Response({
                        'error': f'{usuario.nombre} {usuario.apellido} no tiene un ingreso registrado para el evento {evento.nombre} hoy.'
                    }, status=status.HTTP_400_BAD_REQUEST)
                if existing_asistencia.hora_salida:
                    return Response({
                        'error': f'{usuario.nombre} {usuario.apellido} ya registró su salida para el evento {evento.nombre} hoy a las {existing_asistencia.hora_salida}.'
                    }, status=status.HTTP_400_BAD_REQUEST)
                current_datetime = datetime.combine(today, current_time)
                ingreso_datetime = datetime.combine(today, existing_asistencia.hora_ingreso)
                if current_datetime < ingreso_datetime:
                    return Response({
                        'error': f'La hora de salida no puede ser anterior a la hora de ingreso ({existing_asistencia.hora_ingreso}).'
                    }, status=status.HTTP_400_BAD_REQUEST)
                time_difference = current_datetime - ingreso_datetime
                if time_difference < timedelta(minutes=5):
                    return Response({
                        'error': 'Debe haber al menos 5 minutos entre el ingreso y la salida.'
                    }, status=status.HTTP_400_BAD_REQUEST)
                existing_asistencia.hora_salida = current_time
                existing_asistencia.save()
                asistencia = existing_asistencia
                message = f'Salida registrada para {usuario.nombre} {usuario.apellido} en el evento {evento.nombre if evento else "sin evento"} con éxito.'
                hora = asistencia.hora_salida
            else:
                return Response({
                    'error': 'Tipo de escaneo no válido. Debe ser "ingreso" o "salida".'
                }, status=status.HTTP_400_BAD_REQUEST)

            LogAccion.objects.create(
                usuario=request.user,
                accion=f"Registrar {tipo_escaneo}",
                descripcion=f"{request.user.username} registró {tipo_escaneo} para {usuario.nombre} {usuario.apellido} en el evento {evento.nombre if evento else 'sin evento'}."
            )

            return Response({
                'message': message,
                'hora': hora.strftime('%H:%M:%S') if hora else 'No registrado',
                'nombre': f"{usuario.nombre} {usuario.apellido}",
                'foto_perfil': request.build_absolute_uri(usuario.foto_perfil.url) if usuario.foto_perfil else None
            }, status=status.HTTP_201_CREATED)
        except Usuario.DoesNotExist:
            print("Error: Usuario no encontrado")
            return Response({'error': 'El DNI escaneado no corresponde a ningún usuario registrado.'}, status=status.HTTP_404_NOT_FOUND)
        except Evento.DoesNotExist:
            print("Error: Evento no encontrado o no activo")
            return Response({'error': 'El evento seleccionado no es válido o no está activo.'}, status=status.HTTP_400_BAD_REQUEST)
        except Ubicacion.DoesNotExist:
            print("Error: Ubicación no encontrada")
            return Response({'error': 'La ubicación seleccionada no es válida.'}, status=status.HTTP_400_BAD_REQUEST)
        except base64.binascii.Error:
            print("Error: QR inválido")
            return Response({'error': 'El código QR escaneado es inválido.'}, status=status.HTTP_400_BAD_REQUEST)

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

@login_required
def historial_asistencias(request):
    form = FiltroAsistenciaForm(request.GET or None)
    filters = {}
    if form.is_valid():
        filters = form.cleaned_data
    
    # Usar el utility para obtener datos filtrados
    data = get_filtered_attendance_data(filters)
    asistencias = data['asistencias']
    usuarios_no_asistentes = data['usuarios_no_asistentes']
    
    # --- Estadísticas para el Dashboard ---
    # 1. Resumen de contadores
    total_registros = asistencias.count()
    confirmadas = asistencias.filter(confirmada=True).count()
    pendientes = total_registros - confirmadas
    total_inasistencias = usuarios_no_asistentes.count() if usuarios_no_asistentes else 0
    

    paginator = Paginator(asistencias, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'form': form,
        'usuarios_no_asistentes': usuarios_no_asistentes,
        'can_scan_qr': request.user.has_perm('asistencia.can_scan_qr'),
        'stats': {
            'total': total_registros,
            'confirmadas': confirmadas,
            'pendientes': pendientes,
            'inasistencias': total_inasistencias,
        },
    }
    return render(request, 'asistencia/historial_asistencias.html', context)

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def exportar_asistencias_csv(request):
    form = FiltroAsistenciaForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    data = get_filtered_attendance_data(filters)
    return generate_attendance_csv(data['asistencias'], data['usuarios_no_asistentes'])

@login_required
@permission_required('asistencia.can_manage_users', raise_exception=True)
def exportar_asistencias_excel(request):
    form = FiltroAsistenciaForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    data = get_filtered_attendance_data(filters)
    
    LogAccion.objects.create(
        usuario=request.user,
        accion="Exportar Excel",
        descripcion=f"{request.user.username} exportó las asistencias a Excel."
    )
    return generate_attendance_excel(data['asistencias'], data['usuarios_no_asistentes'])

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
    
    asistencias = data['asistencias']
    no_asistentes = data['usuarios_no_asistentes']
    
    total_usuarios = Usuario.objects.count()
    total_asistentes = asistencias.count()
    porcentaje_asistencia = (total_asistentes / total_usuarios * 100) if total_usuarios > 0 else 0

    context = {
        'asistencias': asistencias,
        'no_asistentes': no_asistentes,
        'total_usuarios': total_usuarios,
        'total_asistentes': total_asistentes,
        'total_inasistentes': no_asistentes.count() if no_asistentes else 0,
        'porcentaje_asistencia': porcentaje_asistencia,
        'logo_base64': get_logo_base64(),
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'filtros': {
            'fecha_inicio': filters.get('fecha_inicio').strftime('%d/%m/%Y') if filters.get('fecha_inicio') else 'Inicio',
            'fecha_fin': filters.get('fecha_fin').strftime('%d/%m/%Y') if filters.get('fecha_fin') else 'Hoy',
            'evento': filters.get('evento'),
            'dni': filters.get('dni')
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
    asistencias = data['asistencias']
    no_asistentes = data['usuarios_no_asistentes']
    
    total_usuarios = Usuario.objects.count()
    total_asistentes = asistencias.count()
    porcentaje_asistencia = (total_asistentes / total_usuarios * 100) if total_usuarios > 0 else 0
    total_inasistentes = no_asistentes.count() if no_asistentes else 0
    
    context = {
        'evento': evento,
        'asistencias': asistencias,
        'no_asistentes': no_asistentes,
        'total_usuarios': total_usuarios,
        'porcentaje_asistencia': porcentaje_asistencia,
        'current_date': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'logo_base64': get_logo_base64(),
        'total_asistentes': total_asistentes,
        'total_inasistentes': total_inasistentes,
        'filtros': {
            'dni': filters.get('dni'),
            'fecha_inicio': filters.get('fecha_inicio').strftime('%d/%m/%Y') if filters.get('fecha_inicio') else None,
            'fecha_fin': filters.get('fecha_fin').strftime('%d/%m/%Y') if filters.get('fecha_fin') else None,
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
            usuario = form.save(commit=False)
            if usuario.fecha_nacimiento:
                today = date.today()
                age = today.year - usuario.fecha_nacimiento.year - ((today.month, today.day) < (usuario.fecha_nacimiento.month, usuario.fecha_nacimiento.day))
                if age >= 65:
                    usuario.estado = Usuario.ESTADO_EXONERADO
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