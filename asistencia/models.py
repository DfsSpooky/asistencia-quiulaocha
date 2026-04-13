from django.db import models
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.templatetags.static import static
import qrcode
from io import BytesIO
import re
import uuid
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models.fields.files import FieldFile
from PIL import Image, ImageOps
from datetime import date, datetime
from .qr_security import build_qr_encoded_payload


def validate_file_size(value):
    max_size = 5 * 1024 * 1024
    if value and getattr(value, 'size', 0) > max_size:
        raise ValidationError(f'El archivo no puede superar los {max_size // (1024 * 1024)} MB.')


def validate_image_content(value):
    if not value:
        return
    try:
        value.seek(0)
        with Image.open(value) as img:
            img.verify()
        value.seek(0)
    except Exception as exc:
        raise ValidationError('La imagen subida es inválida o está dañada.') from exc


def validate_supporting_document_content(value):
    if not value:
        return

    name = (getattr(value, 'name', '') or '').lower()
    try:
        value.seek(0)
        header = value.read(16)
        value.seek(0)
    except Exception as exc:
        raise ValidationError('No se pudo validar el archivo adjunto.') from exc

    if name.endswith('.pdf'):
        if not header.startswith(b'%PDF'):
            raise ValidationError('El PDF adjunto no es válido.')
        return

    if name.endswith(('.jpg', '.jpeg', '.png')):
        validate_image_content(value)
        return

    raise ValidationError('Tipo de archivo no permitido.')

class Ubicacion(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)

    def __str__(self):
        return self.nombre

class Evento(models.Model):
    nombre = models.CharField(max_length=100)
    fecha = models.DateField()
    descripcion = models.TextField(blank=True)
    hora_ingreso = models.TimeField(default='08:00', help_text="Hora de ingreso programada")
    activo = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)

        # Si es un evento nuevo, registrar asistencias automáticas para usuarios EXONERADOS
        if is_new:
            usuarios_exonerados = Usuario.objects.filter(estado=Usuario.ESTADO_EXONERADO)
            # Capturar la hora actual una sola vez para consistencia
            hora_actual = datetime.now().time()
            # Crear lista de instancias de Asistencia en memoria
            asistencias = [
                Asistencia(
                    usuario=usuario,
                    fecha=self.fecha,
                    evento=self,
                    hora_ingreso=hora_actual,
                    confirmada=True
                )
                for usuario in usuarios_exonerados
            ]
            # Insertar todas las asistencias en una sola consulta SQL
            if asistencias:
                Asistencia.objects.bulk_create(asistencias)

    def __str__(self):
        return f"{self.nombre} ({self.fecha})"

class Usuario(models.Model):
    FOTO_PERFIL_SIZE = (320, 320)
    FOTO_PERFIL_QUALITY = 72
    ESTADO_ACTIVO = 'ACTIVO'
    ESTADO_PASIVO = 'PASIVO'
    ESTADO_EXONERADO = 'EXONERADO'
    ESTADOS = [
        (ESTADO_ACTIVO, 'Activo'),
        (ESTADO_PASIVO, 'Pasivo'),
        (ESTADO_EXONERADO, 'Exonerado'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    dni = models.CharField(max_length=8, unique=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADOS, default=ESTADO_ACTIVO)
    qr_uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    qr_version = models.PositiveIntegerField(default=1)
    qr_code = models.ImageField(upload_to='qr_codes/', blank=True)
    foto_perfil = models.ImageField(
        upload_to='perfil_fotos/',
        blank=True,
        null=True,
        validators=[validate_file_size, validate_image_content],
    )

    @property
    def foto_perfil_url(self):
        if self.foto_perfil:
            try:
                if self.foto_perfil.storage.exists(self.foto_perfil.name):
                    return self.foto_perfil.url
            except (OSError, ValueError):
                pass
        return static('images/default_avatar.svg')

    def clean(self):
        if not self.dni.isdigit() or len(self.dni) != 8:
            raise ValidationError({'dni': 'El DNI debe tener exactamente 8 dígitos numéricos.'})
        if not re.match(r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$', self.nombre):
            raise ValidationError({'nombre': 'El nombre solo puede contener letras y espacios.'})
        if not re.match(r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$', self.apellido):
            raise ValidationError({'apellido': 'El apellido solo puede contener letras y espacios.'})

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)

        is_new = self.pk is None
        original_dni = None
        original_qr_version = None
        original_foto_perfil = None
        if not is_new:
            original_dni, original_qr_version, original_foto_perfil = (
                Usuario.objects.filter(pk=self.pk).values_list('dni', 'qr_version', 'foto_perfil').first()
            )
        dni_changed = (original_dni is not None and original_dni != self.dni)
        qr_version_changed = (original_qr_version is not None and original_qr_version != self.qr_version)
        replacing_photo = bool(
            self.foto_perfil and (
                is_new or
                not isinstance(self.foto_perfil, FieldFile) or
                self.foto_perfil.name != (original_foto_perfil or '')
            )
        )

        self.full_clean()

        if self.foto_perfil:
            try:
                img = Image.open(self.foto_perfil)
                img = ImageOps.exif_transpose(img).convert('RGB')
                # Genera un recorte centrado optimizado para avatar y reduce el peso final.
                img = ImageOps.fit(img, self.FOTO_PERFIL_SIZE, Image.Resampling.LANCZOS)
                buffer = BytesIO()
                img.save(
                    buffer,
                    format='JPEG',
                    quality=self.FOTO_PERFIL_QUALITY,
                    optimize=True,
                    progressive=True,
                )
                buffer.seek(0)
                target_photo_name = f'perfil_{self.dni}.jpg'
                if replacing_photo and original_foto_perfil:
                    self.foto_perfil.storage.delete(original_foto_perfil)
                self.foto_perfil.save(
                    target_photo_name,
                    ContentFile(buffer.getvalue()),
                    save=False
                )
                if update_fields is not None:
                    update_fields.add('foto_perfil')
            except Exception:
                self.foto_perfil = None

        if not self.foto_perfil:
            self.foto_perfil = None

        qr_needs_refresh = is_new or qr_version_changed or not self.qr_code
        if qr_needs_refresh:
            qr = qrcode.QRCode(version=1, box_size=5, border=2)
            # Firma estable entre servidores con clave dedicada y payload versionado.
            encoded_qr_payload = build_qr_encoded_payload(self.qr_uid, self.qr_version)
            qr.add_data(encoded_qr_payload)
            qr.make(fit=True)
            img = qr.make_image(fill='black', back_color='white')
            buffer = BytesIO()
            img.save(buffer, format='PNG', quality=70)
            self.qr_code.save(f'qr_{self.qr_uid}_v{self.qr_version}.png', ContentFile(buffer.getvalue()), save=False)
            if update_fields is not None:
                update_fields.add('qr_code')

        if update_fields is not None:
            kwargs['update_fields'] = list(update_fields)

        super().save(*args, **kwargs)

    def rotate_qr(self):
        self.qr_version += 1
        self.save(update_fields=['qr_version'])

    def __str__(self):
        return f"{self.nombre} {self.apellido} ({self.dni})"

    class Meta:
        permissions = [
            ("can_manage_users", "Can manage users"),
            ("can_scan_qr", "Can scan QR codes"),
        ]
        indexes = [
            models.Index(fields=['dni']),
        ]

class Asistencia(models.Model):
    PUNTUALIDAD_PUNTUAL = 'PUNTUAL'
    PUNTUALIDAD_TARDE = 'TARDE'
    PUNTUALIDAD_NO_APLICA = 'NO_APLICA'
    PUNTUALIDAD_CHOICES = [
        (PUNTUALIDAD_PUNTUAL, 'Puntual'),
        (PUNTUALIDAD_TARDE, 'Tardanza'),
        (PUNTUALIDAD_NO_APLICA, 'No aplica'),
    ]

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    fecha = models.DateField(default=timezone.localdate)
    hora_ingreso = models.TimeField(null=True, blank=True)
    hora_salida = models.TimeField(null=True, blank=True)
    ubicacion = models.ForeignKey(Ubicacion, on_delete=models.SET_NULL, null=True, blank=True)
    evento = models.ForeignKey(Evento, on_delete=models.SET_NULL, null=True, blank=True)
    confirmada = models.BooleanField(default=False)
    es_justificada = models.BooleanField(default=False, help_text="Indica si la inasistencia fue justificada")
    puntualidad = models.CharField(
        max_length=12,
        choices=PUNTUALIDAD_CHOICES,
        default=PUNTUALIDAD_NO_APLICA,
        help_text="Clasifica si el ingreso fue puntual o con tardanza.",
    )

    def __str__(self):
        ingreso = self.hora_ingreso.strftime('%H:%M:%S') if self.hora_ingreso else 'No registrado'
        salida = self.hora_salida.strftime('%H:%M:%S') if self.hora_salida else 'No registrado'
        return f"Asistencia de {self.usuario} el {self.fecha} - Ingreso: {ingreso}, Salida: {salida} {'(Confirmada)' if self.confirmada else ''}"

    class Meta:
        indexes = [
            models.Index(fields=['fecha']),
            models.Index(fields=['usuario']),
            models.Index(fields=['evento']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'evento', 'fecha'],
                condition=models.Q(evento__isnull=False),
                name='uniq_asistencia_usuario_evento_fecha',
            ),
        ]

class Justificacion(models.Model):
    ESTADO_PENDIENTE = 'PENDIENTE'
    ESTADO_APROBADO = 'APROBADO'
    ESTADO_RECHAZADO = 'RECHAZADO'
    ESTADOS = [
        (ESTADO_PENDIENTE, 'Pendiente'),
        (ESTADO_APROBADO, 'Aprobada'),
        (ESTADO_RECHAZADO, 'Rechazada'),
    ]

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='justificaciones')
    evento = models.ForeignKey(Evento, on_delete=models.CASCADE, related_name='justificaciones')
    motivo = models.TextField(verbose_name="Motivo de la inasistencia")
    evidencia = models.FileField(
        upload_to='justificaciones/', 
        blank=True, 
        null=True, 
        verbose_name="Evidencia (Foto/Documento)",
        validators=[
            FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png']),
            validate_file_size,
            validate_supporting_document_content,
        ]
    )
    estado = models.CharField(max_length=10, choices=ESTADOS, default=ESTADO_PENDIENTE)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    procesado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='justificaciones_procesadas')
    comentario_admin = models.TextField(blank=True, help_text="Comentario opcional del administrador", verbose_name="Comentario del Admin")

    def __str__(self):
        return f"Justificación de {self.usuario} - {self.evento.nombre} ({self.get_estado_display()})"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_estado = None
        if not is_new:
            old_estado = Justificacion.objects.get(pk=self.pk).estado
        
        super().save(*args, **kwargs)

        # Si se aprueba, crear o marcar la asistencia como justificada
        if self.estado == self.ESTADO_APROBADO and (is_new or old_estado != self.ESTADO_APROBADO):
            asistencia, created = Asistencia.objects.get_or_create(
                usuario=self.usuario,
                evento=self.evento,
                defaults={'confirmada': True, 'es_justificada': True, 'fecha': self.evento.fecha}
            )
            if not created:
                asistencia.es_justificada = True
                asistencia.confirmada = True
                asistencia.save()
        
        # Si deja de estar aprobada (ej. RECHAZADA), debe volver a computar como falta real.
        elif self.estado != self.ESTADO_APROBADO and old_estado == self.ESTADO_APROBADO:
            asistencia = Asistencia.objects.filter(usuario=self.usuario, evento=self.evento).first()
            if asistencia:
                # Si el registro fue creado automáticamente por la aprobación de justificación
                # (sin ingreso/salida reales), lo eliminamos para que reportes lo cuenten como FALTA.
                if not asistencia.hora_ingreso and not asistencia.hora_salida:
                    asistencia.delete()
                else:
                    # Si hubo asistencia real, solo retiramos el estado de justificada.
                    asistencia.es_justificada = False
                    asistencia.save(update_fields=['es_justificada'])

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'evento'],
                name='uniq_justificacion_usuario_evento',
            ),
        ]

class LogAccion(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    accion = models.CharField(max_length=100)
    descripcion = models.TextField()
    fecha = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.accion} por {self.usuario} el {self.fecha}"

    class Meta:
        indexes = [
            models.Index(fields=['fecha']),
            models.Index(fields=['usuario']),
        ]

class ConfiguracionSistema(models.Model):
    logo = models.ImageField(
        upload_to='logos/',
        blank=True,
        null=True,
        help_text="Logo del sistema (se mostrará en la barra de navegación, login y reportes)",
        validators=[validate_file_size, validate_image_content],
    )
    nombre_institucion = models.CharField(
        max_length=100,
        default='QUIULACOCHA',
        help_text="Nombre de la institución (se mostrará en toda la aplicación)"
    )
    tardanza_activa = models.BooleanField(
        default=True,
        help_text="Activa el control de tardanzas para marcar faltas cuando se supera el límite."
    )
    tolerancia_minutos = models.PositiveIntegerField(
        default=15,
        help_text="Tiempo de tolerancia en minutos para el ingreso antes de considerarse tardanza (si aplica)"
    )

    def save(self, *args, **kwargs):
        if ConfiguracionSistema.objects.exists() and not self.pk:
            raise ValidationError("Solo puede existir una configuración del sistema. Edita la existente en lugar de crear una nueva.")
        super().save(*args, **kwargs)

        if self.logo:
            try:
                img = Image.open(self.logo)
                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    img = img.convert('RGBA')
                else:
                    img = img.convert('RGB')
                img = img.resize((150, 150), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                img.save(buffer, format='PNG', quality=70)
                buffer.seek(0)
                self.logo.save('logo.png', ContentFile(buffer.getvalue()), save=False)
                super().save(*args, **kwargs)
            except Exception as e:
                print(f"Error al procesar el logo: {e}")

    def __str__(self):
        return "Configuración del Sistema"

    class Meta:
        verbose_name = "Configuración del Sistema"
        verbose_name_plural = "Configuración del Sistema"

class HistorialCarnet(models.Model):
    MOTIVO_CHOICES = [
        ('Primer Carnet', 'Primer Carnet'),
        ('Renovación por Vencimiento', 'Renovación por Vencimiento'),
        ('Reposición por Pérdida/Robo', 'Reposición por Pérdida/Robo'),
        ('Reposición por Deterioro', 'Reposición por Deterioro'),
    ]
    ESTADO_CHOICES = [
        ('Activo', 'Activo'),
        ('Inactivo/Anulado', 'Inactivo/Anulado'),
    ]

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='historial_carnets')
    fecha_emision = models.DateTimeField(default=timezone.now)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    motivo = models.CharField(max_length=50, choices=MOTIVO_CHOICES)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='Activo')
    observaciones = models.TextField(blank=True, null=True)
    entregado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.estado == 'Activo':
            HistorialCarnet.objects.filter(usuario=self.usuario, estado='Activo').exclude(pk=self.pk).update(estado='Inactivo/Anulado')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Carnet {self.motivo} - {self.usuario} ({self.estado})"
