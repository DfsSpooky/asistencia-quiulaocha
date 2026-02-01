from django.db import models
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
import qrcode
from io import BytesIO
import base64
import re
from django.contrib.auth.models import User
from PIL import Image
from datetime import date, datetime

class Ubicacion(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)

    def __str__(self):
        return self.nombre

class Evento(models.Model):
    nombre = models.CharField(max_length=100)
    fecha = models.DateField()
    descripcion = models.TextField(blank=True)
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
    qr_code = models.ImageField(upload_to='qr_codes/', blank=True)
    foto_perfil = models.ImageField(upload_to='perfil_fotos/', blank=True, null=True)

    def clean(self):
        if not self.dni.isdigit() or len(self.dni) != 8:
            raise ValidationError({'dni': 'El DNI debe tener exactamente 8 dígitos numéricos.'})
        if not re.match(r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$', self.nombre):
            raise ValidationError({'nombre': 'El nombre solo puede contener letras y espacios.'})
        if not re.match(r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$', self.apellido):
            raise ValidationError({'apellido': 'El apellido solo puede contener letras y espacios.'})

    def save(self, *args, **kwargs):
        self.full_clean()

        if self.foto_perfil:
            try:
                img = Image.open(self.foto_perfil)
                img = img.convert('RGB')
                img = img.resize((200, 200), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=70)
                buffer.seek(0)
                self.foto_perfil.save(
                    f'perfil_{self.dni}.jpg',
                    ContentFile(buffer.getvalue()),
                    save=False
                )
            except Exception as e:
                self.foto_perfil = 'images/default_avatar.png'

        if not self.foto_perfil:
            self.foto_perfil = 'images/default_avatar.png'

        qr = qrcode.QRCode(version=1, box_size=5, border=2)
        encoded_dni = base64.b64encode(self.dni.encode()).decode()
        qr.add_data(encoded_dni)
        qr.make(fit=True)
        img = qr.make_image(fill='black', back_color='white')
        buffer = BytesIO()
        img.save(buffer, format='PNG', quality=70)
        self.qr_code.save(f'qr_{self.dni}.png', ContentFile(buffer.getvalue()), save=False)

        super().save(*args, **kwargs)

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
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    fecha = models.DateField(auto_now_add=True)
    hora_ingreso = models.TimeField(null=True, blank=True)
    hora_salida = models.TimeField(null=True, blank=True)
    ubicacion = models.ForeignKey(Ubicacion, on_delete=models.SET_NULL, null=True, blank=True)
    evento = models.ForeignKey(Evento, on_delete=models.SET_NULL, null=True, blank=True)
    confirmada = models.BooleanField(default=False)
    es_justificada = models.BooleanField(default=False, help_text="Indica si la inasistencia fue justificada")

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
    evidencia = models.ImageField(
        upload_to='justificaciones/', 
        blank=True, 
        null=True, 
        verbose_name="Evidencia (Foto/Documento)",
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])]
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
        
        # Si se rechaza y antes estaba aprobada, quitar el flag de justificada (opcional)
        elif self.estado != self.ESTADO_APROBADO and old_estado == self.ESTADO_APROBADO:
            asistencia = Asistencia.objects.filter(usuario=self.usuario, evento=self.evento).first()
            if asistencia:
                asistencia.es_justificada = False
                asistencia.save()

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
    logo = models.ImageField(upload_to='logos/', blank=True, null=True, help_text="Logo del sistema (se mostrará en la barra de navegación, login y reportes)")
    nombre_institucion = models.CharField(
        max_length=100,
        default='QUIULACOCHA',
        help_text="Nombre de la institución (se mostrará en toda la aplicación)"
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