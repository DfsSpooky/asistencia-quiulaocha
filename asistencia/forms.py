from django import forms
from .models import Evento, Ubicacion, Usuario, Justificacion

class JustificacionForm(forms.ModelForm):
    class Meta:
        model = Justificacion
        fields = ['evento', 'motivo', 'evidencia']
        widgets = {
            'evento': forms.Select(),
            'motivo': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Explica brevemente por qué no pudiste asistir...'
            }),
            'evidencia': forms.ClearableFileInput(),
        }

class AdminJustificacionForm(JustificacionForm):
    usuario = forms.ModelChoiceField(
        # Optimize queryset: only load necessary fields to reduce memory usage
        queryset=Usuario.objects.only('id', 'nombre', 'apellido', 'dni').order_by('apellido', 'nombre'),
        label='Socio a Justificar',
        widget=forms.Select()
    )

    class Meta(JustificacionForm.Meta):
        fields = ['usuario', 'evento', 'motivo', 'evidencia']

class FiltroAsistenciaForm(forms.Form):
    dni = forms.CharField(required=False, label='DNI')
    fecha_inicio = forms.DateField(required=False, label='Fecha Inicio', widget=forms.DateInput(attrs={'type': 'date'}))
    fecha_fin = forms.DateField(required=False, label='Fecha Fin', widget=forms.DateInput(attrs={'type': 'date'}))
    evento = forms.ModelChoiceField(queryset=Evento.objects.all(), required=False, label='Evento')
    ubicacion = forms.ModelChoiceField(queryset=Ubicacion.objects.all(), required=False, label='Ubicación')
    confirmada = forms.ChoiceField(
        choices=[('', 'Todos'), ('true', 'Confirmada'), ('false', 'No Confirmada')],
        required=False,
        label='Confirmada'
    )
    estado = forms.ChoiceField(
        choices=[('', 'Todos'), ('asistieron', 'Asistieron'), ('faltaron', 'Faltaron')],
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
