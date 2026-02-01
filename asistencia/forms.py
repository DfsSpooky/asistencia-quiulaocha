from django import forms
from .models import Evento, Ubicacion, Usuario, Justificacion

class JustificacionForm(forms.ModelForm):
    class Meta:
        model = Justificacion
        fields = ['evento', 'motivo', 'evidencia']
        widgets = {
            'evento': forms.Select(attrs={'class': 'select2-searchable w-full px-5 py-4 bg-slate-50 border border-slate-200 rounded-2xl focus:bg-white focus:ring-4 focus:ring-primary-500/10 focus:border-primary-500 outline-none transition-all font-bold text-slate-700'}),
            'motivo': forms.Textarea(attrs={'class': 'w-full px-5 py-4 bg-slate-50 border border-slate-200 rounded-2xl focus:bg-white focus:ring-4 focus:ring-primary-500/10 focus:border-primary-500 outline-none transition-all font-bold text-slate-700', 'rows': 4, 'placeholder': 'Explica brevemente por qué no pudiste asistir...'}),
            'evidencia': forms.ClearableFileInput(attrs={'class': 'w-full px-5 py-4 bg-slate-50 border border-slate-200 rounded-2xl focus:bg-white focus:ring-4 focus:ring-primary-500/10 focus:border-primary-500 outline-none transition-all font-bold text-slate-700'}),
        }

class AdminJustificacionForm(JustificacionForm):
    usuario = forms.ModelChoiceField(
        queryset=Usuario.objects.all().order_by('apellido', 'nombre'),
        label='Socio a Justificar',
        widget=forms.Select(attrs={'class': 'select2-searchable w-full px-5 py-4 bg-slate-50 border border-slate-200 rounded-2xl focus:bg-white focus:ring-4 focus:ring-primary-500/10 focus:border-primary-500 outline-none transition-all font-bold text-slate-700'})
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