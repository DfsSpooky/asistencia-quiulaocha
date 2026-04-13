from django import forms
from django.contrib.auth.password_validation import validate_password
from .models import Evento, Ubicacion, Usuario, Justificacion, HistorialCarnet

class JustificacionForm(forms.ModelForm):
    class Meta:
        model = Justificacion
        fields = ['evento', 'motivo', 'evidencia']
        widgets = {
            'evento': forms.Select(attrs={'class': 'select2-searchable'}),
            'motivo': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Explica brevemente por qué no pudiste asistir...',
                'class': 'w-full px-5 py-4 bg-slate-50 border-2 border-slate-50 rounded-2xl focus:bg-white focus:border-indigo-500 outline-none transition-all font-bold text-slate-700 text-sm'
            }),
            'evidencia': forms.FileInput(attrs={'class': 'w-full px-5 py-3 bg-slate-50 border-2 border-slate-50 rounded-2xl font-bold text-slate-400 text-sm'}),
        }

class AdminJustificacionForm(JustificacionForm):
    usuario = forms.ModelChoiceField(
        queryset=Usuario.objects.only('id', 'nombre', 'apellido', 'dni').order_by('apellido', 'nombre'),
        label='Socio a Justificar',
        widget=forms.Select(attrs={'class': 'select2-searchable'})
    )

    class Meta(JustificacionForm.Meta):
        fields = ['usuario', 'evento', 'motivo', 'evidencia']

    def clean_usuario(self):
        usuario = self.cleaned_data.get('usuario')
        if usuario and usuario.estado == Usuario.ESTADO_EXONERADO:
            raise forms.ValidationError(
                'Este usuario es exonerado y no se puede proceder con una justificación.'
            )
        return usuario

class FiltroAsistenciaForm(forms.Form):
    dni = forms.CharField(required=False, label='DNI')
    fecha_inicio = forms.DateField(required=False, label='Fecha Inicio', widget=forms.DateInput(attrs={'type': 'date'}))
    fecha_fin = forms.DateField(required=False, label='Fecha Fin', widget=forms.DateInput(attrs={'type': 'date'}))
    evento = forms.ModelChoiceField(queryset=Evento.objects.all().order_by('-fecha'), required=False, label='Evento')
    ubicacion = forms.ModelChoiceField(queryset=Ubicacion.objects.all(), required=False, label='Ubicación')
    confirmada = forms.ChoiceField(
        choices=[('', 'Todos'), ('true', 'Confirmada'), ('false', 'No Confirmada')],
        required=False,
        label='Confirmada'
    )
    estado = forms.ChoiceField(
        choices=[
            ('', 'Todos'),
            ('asistieron', 'Asistieron'),
            ('pendientes', 'Pendientes (Sin Salida)'),
            ('faltaron', 'Faltaron (Todas)'),
            ('faltas_justificadas', 'Faltas Justificadas'),
            ('faltas_injustificadas', 'Faltas Injustificadas'),
        ],
        required=False,
        label='Estado'
    )
    ordenar_por = forms.ChoiceField(
        choices=[
            ('-fecha', 'Fecha (Descendente)'),
            ('fecha', 'Fecha (Ascendente)'),
            ('hora_ingreso', 'Hora de Ingreso (Ascendente)'),
            ('-hora_ingreso', 'Hora de Ingreso (Descendente)'),
            ('hora_salida', 'Hora de Salida (Ascendente)'),
            ('-hora_salida', 'Hora de Salida (Descendente)')
        ],
        required=False,
        label='Ordenar Por'
    )

    def clean(self):
        cleaned_data = super().clean()
        fecha_inicio = cleaned_data.get('fecha_inicio')
        fecha_fin = cleaned_data.get('fecha_fin')

        if fecha_inicio and fecha_fin and fecha_inicio > fecha_fin:
            raise forms.ValidationError('La fecha inicio no puede ser mayor que la fecha fin.')

        return cleaned_data

class ImportarUsuariosForm(forms.Form):
    archivo_csv = forms.FileField(label='Archivo CSV')

class BuscarUsuarioForm(forms.Form):
    query = forms.CharField(required=False, label='Buscar')
    estado = forms.ChoiceField(
        choices=[
            ('', 'Todos'),
            (Usuario.ESTADO_ACTIVO, 'Activo'),
            (Usuario.ESTADO_PASIVO, 'Pasivo'),
            (Usuario.ESTADO_EXONERADO, 'Exonerado'),
        ],
        required=False,
        label='Estado'
    )
    ordenar_por = forms.ChoiceField(
        choices=[
            ('nombre', 'Nombre (Ascendente)'),
            ('-nombre', 'Nombre (Descendente)'),
            ('apellido', 'Apellido (Ascendente)'),
            ('-apellido', 'Apellido (Descendente)'),
            ('dni', 'DNI (Ascendente)'),
            ('-dni', 'DNI (Descendente)'),
        ],
        required=False,
        label='Ordenar Por'
    )

class UsuarioRegistroForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, label='Contraseña')
    password_confirm = forms.CharField(widget=forms.PasswordInput, label='Confirmar Contraseña')
    es_escaneador = forms.BooleanField(required=False, label='¿Asignar rol de Escaneador?')

    class Meta:
        model = Usuario
        fields = ['nombre', 'apellido', 'dni', 'fecha_nacimiento', 'estado', 'foto_perfil']
        widgets = {
            'fecha_nacimiento': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')

        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError("Las contraseñas no coinciden.")
        if password:
            validate_password(password)
        return cleaned_data

    def save(self, commit=True):
        """
        Override save to centralize business logic for EXONERADO status.
        Users >= 65 years old are automatically set to EXONERADO state.
        """
        usuario = super().save(commit=False)
        
        # Business rule: Users >= 65 years old are automatically EXONERADO
        if usuario.fecha_nacimiento:
            from datetime import date
            today = date.today()
            age = today.year - usuario.fecha_nacimiento.year - (
                (today.month, today.day) < (usuario.fecha_nacimiento.month, usuario.fecha_nacimiento.day)
            )
            if age >= 65:
                usuario.estado = Usuario.ESTADO_EXONERADO
        
        if commit:
            usuario.save()
        return usuario

class CarnetForm(forms.ModelForm):
    class Meta:
        model = HistorialCarnet
        fields = ['motivo', 'fecha_vencimiento', 'observaciones']
        widgets = {
            'motivo': forms.Select(attrs={'class': 'form-control w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 bg-white'}),
            'fecha_vencimiento': forms.DateInput(attrs={'type': 'date', 'class': 'form-control w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 bg-white'}),
            'observaciones': forms.Textarea(attrs={'class': 'form-control w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 bg-white', 'rows': 3}),
        }

class AdminCarnetForm(CarnetForm):
    usuario = forms.ModelChoiceField(
        queryset=Usuario.objects.only('id', 'nombre', 'apellido', 'dni').order_by('apellido', 'nombre'),
        label='Socio',
        widget=forms.Select(attrs={'class': 'select2-searchable w-full'})
    )

    class Meta(CarnetForm.Meta):
        fields = ['usuario', 'motivo', 'fecha_vencimiento', 'observaciones']

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_file_clean = super().clean

        if isinstance(data, (list, tuple)):
            if not data:
                return []
            return [single_file_clean(item, initial) for item in data]

        cleaned = single_file_clean(data, initial)
        return [cleaned] if cleaned else []


class CargaMasivaFotosForm(forms.Form):
    fotos = MultipleFileField(
        widget=MultipleFileInput(attrs={'multiple': True, 'accept': '.jpg,.jpeg,.png,.webp,image/*'}),
        label='Seleccione las fotos a cargar',
        help_text='Los nombres de los archivos deben ser el DNI del usuario (ej: 12345678.jpg).',
        required=True
    )

    ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
    MAX_FILE_SIZE = 5 * 1024 * 1024

    def clean(self):
        cleaned_data = super().clean()
        fotos = cleaned_data.get('fotos') or []

        if not fotos:
            raise forms.ValidationError('Debe seleccionar al menos una foto.')

        for foto in fotos:
            nombre = (foto.name or '').strip()
            if '.' not in nombre:
                raise forms.ValidationError(f'El archivo "{nombre}" no tiene extension valida.')

            dni = nombre.rsplit('.', 1)[0].strip()
            extension = f".{nombre.rsplit('.', 1)[1].lower()}"

            if not (dni.isdigit() and len(dni) == 8):
                raise forms.ValidationError(
                    f'El archivo "{nombre}" no cumple el formato de DNI (8 digitos).'
                )

            if extension not in self.ALLOWED_EXTENSIONS:
                raise forms.ValidationError(
                    f'El archivo "{nombre}" no tiene una extension permitida (jpg, jpeg, png, webp).'
                )

            if foto.size > self.MAX_FILE_SIZE:
                raise forms.ValidationError(
                    f'El archivo "{nombre}" supera el tamano maximo de 5 MB.'
                )

        return cleaned_data
