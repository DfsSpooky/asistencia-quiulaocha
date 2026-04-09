"""
Service Layer para la lógica de negocio de asistencias.

Este módulo contiene la clase AsistenciaService que encapsula toda la lógica
de negocio relacionada con el registro de asistencias, separándola de las vistas.
"""

from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from datetime import date, datetime, timedelta
from .models import Usuario, Evento, Asistencia, Ubicacion, LogAccion, ConfiguracionSistema


class AsistenciaService:
    """
    Servicio para manejar la lógica de negocio de asistencias.
    
    Proporciona métodos para registrar ingresos y salidas, validando
    todas las reglas de negocio y manejando excepciones apropiadamente.
    """
    
    @staticmethod
    def registrar_ingreso(usuario, evento, ubicacion, request_user=None, fecha_registro=None):
        """
        Registra el ingreso de un usuario a un evento.
        
        Args:
            usuario (Usuario): El usuario que registra su ingreso
            evento (Evento): El evento al que asiste
            ubicacion (Ubicacion): La ubicación donde se registra
            request_user (User, optional): Usuario que realiza el registro (para logs)
            fecha_registro (datetime, optional): Timestamp original del escaneo (para sync offline)
        
        Returns:
            tuple: (asistencia, message, hora) - La asistencia creada/actualizada, mensaje y hora
        
        Raises:
            ValidationError: Si falla alguna validación de negocio
        """
        # Validaciones
        AsistenciaService._validar_usuario_activo(usuario)
        AsistenciaService._validar_evento_hoy(evento)
        
        # Usar timestamp original si viene de sincronización offline
        if fecha_registro:
            # Timestamp viene del móvil (sincronización offline)
            from django.utils import timezone
            if timezone.is_aware(fecha_registro):
                fecha_registro_local = timezone.localtime(fecha_registro)
            else:
                fecha_registro_local = timezone.make_aware(fecha_registro)
            
            today = fecha_registro_local.date()
            current_time = fecha_registro_local.time()
        else:
            # Escaneo en tiempo real
            from django.utils import timezone
            now = timezone.localtime(timezone.now())
            today = now.date()
            current_time = now.time()
        
        # Buscar asistencia existente
        existing_asistencia = Asistencia.objects.filter(
            usuario=usuario,
            evento=evento,
            fecha=today
        ).first()
        
        # Validar que no haya ingreso duplicado
        AsistenciaService._validar_ingreso_duplicado(existing_asistencia, usuario, evento)
        
        # Registrar ingreso
        if existing_asistencia:
            existing_asistencia.hora_ingreso = current_time
            existing_asistencia.puntualidad = AsistenciaService._calcular_puntualidad(evento, current_time)
            existing_asistencia.save()
            asistencia = existing_asistencia
        else:
            asistencia = Asistencia.objects.create(
                usuario=usuario,
                hora_ingreso=current_time,
                ubicacion=ubicacion,
                evento=evento,
                puntualidad=AsistenciaService._calcular_puntualidad(evento, current_time),
            )
        
        # Registrar log si se proporciona el usuario que realiza la acción
        if request_user:
            AsistenciaService._registrar_log(
                request_user,
                'Registro de Ingreso',
                f'Ingreso registrado para {usuario.nombre} {usuario.apellido} en {evento.nombre if evento else "sin evento"}.'
            )
        
        message = f'Ingreso registrado para {usuario.nombre} {usuario.apellido} en el evento {evento.nombre if evento else "sin evento"} con éxito.'
        return asistencia, message, asistencia.hora_ingreso
    
    @staticmethod
    def registrar_salida(usuario, evento, request_user=None, fecha_registro=None):
        """
        Registra la salida de un usuario de un evento.
        
        Args:
            usuario (Usuario): El usuario que registra su salida
            evento (Evento): El evento del que sale
            request_user (User, optional): Usuario que realiza el registro (para logs)
            fecha_registro (datetime, optional): Timestamp original del escaneo (para sync offline)
        
        Returns:
            tuple: (asistencia, message, hora) - La asistencia actualizada, mensaje y hora
        
        Raises:
            ValidationError: Si falla alguna validación de negocio
        """
        # Validaciones
        AsistenciaService._validar_usuario_activo(usuario)
        AsistenciaService._validar_evento_hoy(evento)
        
        # Usar timestamp original si viene de sincronización offline
        if fecha_registro:
            # Timestamp viene del móvil (sincronización offline)
            from django.utils import timezone
            if timezone.is_aware(fecha_registro):
                fecha_registro_local = timezone.localtime(fecha_registro)
            else:
                fecha_registro_local = timezone.make_aware(fecha_registro)
            
            today = fecha_registro_local.date()
            current_time = fecha_registro_local.time()
        else:
            # Escaneo en tiempo real
            from django.utils import timezone
            now = timezone.localtime(timezone.now())
            today = now.date()
            current_time = now.time()
        
        # Buscar asistencia existente
        existing_asistencia = Asistencia.objects.filter(
            usuario=usuario,
            evento=evento,
            fecha=today
        ).first()
        
        # Validar que existe ingreso previo
        if not existing_asistencia or not existing_asistencia.hora_ingreso:
            raise ValidationError(
                f'{usuario.nombre} {usuario.apellido} no tiene un ingreso registrado para el evento {evento.nombre} hoy.'
            )
        
        # Validar que no haya salida duplicada
        AsistenciaService._validar_salida_duplicada(existing_asistencia, usuario, evento)
        
        # Validar diferencia de tiempo
        AsistenciaService._validar_diferencia_tiempo(
            existing_asistencia.hora_ingreso,
            current_time,
            usuario
        )
        
        # Registrar salida y validar automáticamente
        existing_asistencia.hora_salida = current_time
        existing_asistencia.confirmada = True  # Auto-validación al salida
        existing_asistencia.save()
        
        # Registrar log si se proporciona el usuario que realiza la acción
        if request_user:
            AsistenciaService._registrar_log(
                request_user,
                'Registro de Salida',
                f'Salida registrada para {usuario.nombre} {usuario.apellido} en {evento.nombre if evento else "sin evento"}.'
            )
        
        message = f'Salida registrada para {usuario.nombre} {usuario.apellido} en el evento {evento.nombre if evento else "sin evento"} con éxito.'
        return existing_asistencia, message, existing_asistencia.hora_salida
    
    # ==================== Métodos de Validación ====================
    
    @staticmethod
    def _validar_usuario_activo(usuario):
        """
        Valida que el usuario esté en estado ACTIVO o PASIVO.
        
        Args:
            usuario (Usuario): El usuario a validar
        
        Raises:
            ValidationError: Si el usuario no está activo o pasivo
        """
        if usuario.estado not in [Usuario.ESTADO_ACTIVO, Usuario.ESTADO_PASIVO]:
            raise ValidationError(
                f'El usuario {usuario.nombre} {usuario.apellido} no tiene permiso '
                f'(Estado: {usuario.get_estado_display()}). Solo Activos y Pasivos pueden marcar.'
            )
    
    @staticmethod
    def _validar_evento_hoy(evento):
        """
        Valida que el evento esté programado para hoy.
        
        Args:
            evento (Evento): El evento a validar
        
        Raises:
            ValidationError: Si el evento no es para hoy
        """
        if not evento:
            raise ValidationError(
                'Debes seleccionar un evento activo antes de registrar asistencias.'
            )

        if evento.fecha != date.today():
            raise ValidationError(
                f'El evento {evento.nombre} no está programado para hoy.'
            )
    
    @staticmethod
    def _calcular_puntualidad(evento, hora_ingreso):
        if not evento or not hora_ingreso:
            return Asistencia.PUNTUALIDAD_NO_APLICA

        config = ConfiguracionSistema.objects.first()
        tolerancia = config.tolerancia_minutos if config else 15

        fecha_ref = date.today()
        hora_limite = datetime.combine(fecha_ref, evento.hora_ingreso) + timedelta(minutes=tolerancia)
        ingreso = datetime.combine(fecha_ref, hora_ingreso)
        return Asistencia.PUNTUALIDAD_PUNTUAL if ingreso <= hora_limite else Asistencia.PUNTUALIDAD_TARDE

    @staticmethod
    def _validar_ingreso_duplicado(asistencia, usuario, evento):
        """
        Valida que no exista un ingreso duplicado.
        
        Args:
            asistencia (Asistencia): La asistencia existente (puede ser None)
            usuario (Usuario): El usuario
            evento (Evento): El evento
        
        Raises:
            ValidationError: Si ya existe un ingreso registrado
        """
        if asistencia and asistencia.hora_ingreso:
            raise ValidationError(
                f'{usuario.nombre} {usuario.apellido} ya registró su ingreso para el evento '
                f'{evento.nombre} hoy a las {asistencia.hora_ingreso}.'
            )
    
    @staticmethod
    def _validar_salida_duplicada(asistencia, usuario, evento):
        """
        Valida que no exista una salida duplicada.
        
        Args:
            asistencia (Asistencia): La asistencia existente
            usuario (Usuario): El usuario
            evento (Evento): El evento
        
        Raises:
            ValidationError: Si ya existe una salida registrada
        """
        if asistencia.hora_salida:
            raise ValidationError(
                f'{usuario.nombre} {usuario.apellido} ya registró su salida para el evento '
                f'{evento.nombre} hoy a las {asistencia.hora_salida}.'
            )
    
    @staticmethod
    def _validar_diferencia_tiempo(hora_ingreso, hora_salida, usuario):
        """
        Valida que hayan pasado al menos 5 minutos entre ingreso y salida.
        
        Args:
            hora_ingreso (time): Hora de ingreso
            hora_salida (time): Hora de salida
            usuario (Usuario): El usuario
        
        Raises:
            ValidationError: Si la salida es antes del ingreso o antes de 5 minutos
        """
        today = date.today()
        current_datetime = datetime.combine(today, hora_salida)
        ingreso_datetime = datetime.combine(today, hora_ingreso)
        
        if current_datetime < ingreso_datetime:
            raise ValidationError(
                f'La hora de salida no puede ser anterior a la hora de ingreso ({hora_ingreso}).'
            )
        
        time_difference = current_datetime - ingreso_datetime
        if time_difference < timedelta(minutes=5):
            raise ValidationError(
                'Debe haber al menos 5 minutos entre el ingreso y la salida.'
            )
    
    # ==================== Métodos Helper ====================
    
    @staticmethod
    def _registrar_log(user, accion, descripcion):
        """
        Registra una acción en el log del sistema.
        
        Args:
            user (User): Usuario que realiza la acción
            accion (str): Nombre de la acción
            descripcion (str): Descripción detallada
        """
        LogAccion.objects.create(
            usuario=user,
            accion=accion,
            descripcion=descripcion
        )
