import os
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.urls import reverse, path
from django.template.response import TemplateResponse
from django.shortcuts import redirect
from django.http import JsonResponse
from .models import Usuario, Asistencia, Ubicacion, Evento, LogAccion, ConfiguracionSistema, Justificacion
from .audit import log_critical_change
from .forms import CargaMasivaFotosForm


class UsuarioAdminForm(forms.ModelForm):
    class Meta:
        model = Usuario
        fields = '__all__'
        widgets = {
            'fecha_nacimiento': forms.DateInput(
                format='%Y-%m-%d',
                attrs={
                    'type': 'date',
                    'style': 'padding: 8px 12px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 14px; width: 200px; outline: none; cursor: pointer;'
                }
            ),
            'foto_perfil': forms.ClearableFileInput(
                attrs={
                    'style': 'padding: 8px 12px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 14px; width: min(100%, 320px); background: white;'
                }
            ),
        }


class EventoAdminForm(forms.ModelForm):
    class Meta:
        model = Evento
        fields = '__all__'
        widgets = {
            'fecha': forms.DateInput(
                format='%Y-%m-%d',
                attrs={
                    'type': 'date',
                    'style': 'padding: 8px 12px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 14px; width: 200px; outline: none; cursor: pointer;'
                }
            ),
            'hora_ingreso': forms.TimeInput(
                format='%H:%M',
                attrs={
                    'type': 'time',
                    'style': 'padding: 8px 12px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 14px; width: 200px; outline: none; cursor: pointer;'
                }
            ),
            'descripcion': forms.Textarea(
                attrs={
                    'rows': 3,
                    'style': 'padding: 10px; border-radius: 6px; border: 1px solid #cbd5e1; width: 80%; font-family: inherit; font-size: 14px; outline: none;'
                }
            ),
        }


@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    form = UsuarioAdminForm
    list_display = ('foto_miniatura', 'nombre', 'apellido', 'dni', 'estado_badge', 'editar_usuario')
    search_fields = ('nombre', 'apellido', 'dni')
    readonly_fields = ('foto_perfil_preview', 'qr_code')
    list_display_links = ('nombre', 'apellido', 'dni')
    fieldsets = (
        ('Datos personales', {
            'fields': ('nombre', 'apellido', 'dni', 'fecha_nacimiento', 'estado', 'user')
        }),
        ('Foto de perfil', {
            'fields': ('foto_perfil', 'foto_perfil_preview'),
            'description': 'Sube una imagen para el perfil. La vista previa muestra el resultado actual guardado.'
        }),
        ('Codigo QR', {
            'fields': ('qr_code',),
            'description': 'El codigo QR se genera automaticamente al guardar el usuario.'
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('carga-masiva-fotos/', self.admin_site.admin_view(self.carga_masiva_fotos), name='asistencia_usuario_carga_masiva_fotos'),
        ]
        return custom_urls + urls

    def carga_masiva_fotos(self, request):
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if request.method == 'POST':
            form = CargaMasivaFotosForm(request.POST, request.FILES)
            if form.is_valid():
                fotos = request.FILES.getlist('fotos')
                usuarios_por_dni = {}
                dnis_detectados = set()
                nombres_invalidos = []

                for foto in fotos:
                    nombre_archivo, _ = os.path.splitext((foto.name or '').strip())
                    dni = nombre_archivo.strip()
                    if dni.isdigit() and len(dni) == 8:
                        dnis_detectados.add(dni)
                    else:
                        nombres_invalidos.append(foto.name)

                if dnis_detectados:
                    usuarios_por_dni = Usuario.objects.in_bulk(dnis_detectados, field_name='dni')

                actualizados = 0
                no_encontrados = []
                archivos_invalidos = []

                for foto in fotos:
                    nombre_archivo, _ = os.path.splitext((foto.name or '').strip())
                    dni = nombre_archivo.strip()

                    if dni in usuarios_por_dni:
                        usuario = usuarios_por_dni[dni]
                        try:
                            usuario.foto_perfil = foto
                            usuario.save(update_fields=['foto_perfil'])
                            actualizados += 1
                        except ValidationError:
                            archivos_invalidos.append(foto.name)
                    elif dni.isdigit() and len(dni) == 8:
                        no_encontrados.append(dni)
                    else:
                        nombres_invalidos.append(foto.name)

                dnis_no_encontrados = sorted(set(no_encontrados))
                archivos_nombre_invalido = sorted(set(nombres_invalidos))
                archivos_rechazados = sorted(set(archivos_invalidos))

                if is_ajax:
                    return JsonResponse({
                        'ok': True,
                        'actualizados': actualizados,
                        'total_recibidos': len(fotos),
                        'no_encontrados': dnis_no_encontrados,
                        'nombres_invalidos': archivos_nombre_invalido,
                        'archivos_invalidos': archivos_rechazados,
                    })

                if actualizados > 0:
                    messages.success(request, f'Se actualizaron con éxito las fotos de {actualizados} usuarios.')
                if dnis_no_encontrados:
                    messages.warning(request, f'No se encontraron usuarios para los siguientes DNI: {", ".join(dnis_no_encontrados)}')
                if archivos_nombre_invalido:
                    messages.warning(
                        request,
                        f'Se omitieron archivos con nombre invalido (use 8 digitos de DNI): {", ".join(archivos_nombre_invalido)}'
                    )
                if archivos_rechazados:
                    messages.error(
                        request,
                        f'Se rechazaron archivos por validacion de imagen: {", ".join(archivos_rechazados)}'
                    )

                if not actualizados and not dnis_no_encontrados and not archivos_nombre_invalido and not archivos_rechazados:
                    messages.error(request, 'No se procesó ningún archivo.')

                return redirect('admin:asistencia_usuario_changelist')
            if is_ajax:
                errores = []
                if form.errors:
                    for _, lista_errores in form.errors.items():
                        errores.extend(str(error) for error in lista_errores)
                return JsonResponse({
                    'ok': False,
                    'errors': errores or ['No se pudo procesar la solicitud.'],
                }, status=400)
        else:
            form = CargaMasivaFotosForm()
        
        context = dict(
            self.admin_site.each_context(request),
            form=form,
            title='Carga Masiva de Fotos de Perfil',
            opts=self.model._meta,
        )
        return TemplateResponse(request, 'admin/asistencia/usuario/carga_masiva.html', context)

    def foto_perfil_preview(self, obj):
        if not obj or not obj.pk:
            return "Guarda el usuario para ver la vista previa."
        return format_html(
            '<div style="display:flex; align-items:center; gap:12px;">'
            '<img src="{}" alt="Vista previa" '
            'style="width:88px; height:88px; object-fit:cover; border-radius:18px; border:1px solid #cbd5e1; box-shadow:0 4px 14px rgba(15, 23, 42, 0.08); background:#f8fafc;">'
            '<div style="font-size:12px; color:#64748b;">'
            '<strong style="display:block; color:#0f172a; margin-bottom:4px;">Foto de perfil</strong>'
            'La imagen se procesa automaticamente al guardar.'
            '</div>'
            '</div>',
            obj.foto_perfil_url,
        )
    foto_perfil_preview.short_description = "Vista previa"

    def foto_miniatura(self, obj):
        return format_html(
            '<img src="{}" alt="Foto" '
            'style="width:42px; height:42px; object-fit:cover; border-radius:12px; border:1px solid #cbd5e1; background:#f8fafc; box-shadow:0 2px 10px rgba(15, 23, 42, 0.08);">',
            obj.foto_perfil_url,
        )
    foto_miniatura.short_description = "Foto"

    def estado_badge(self, obj):
        estilos = {
            Usuario.ESTADO_ACTIVO: {
                'bg': '#ecfdf5',
                'text': '#047857',
                'border': '#a7f3d0',
                'dot': '#10b981',
            },
            Usuario.ESTADO_PASIVO: {
                'bg': '#f8fafc',
                'text': '#475569',
                'border': '#cbd5e1',
                'dot': '#64748b',
            },
            Usuario.ESTADO_EXONERADO: {
                'bg': '#ecfeff',
                'text': '#0f766e',
                'border': '#a5f3fc',
                'dot': '#06b6d4',
            },
        }
        style = estilos.get(obj.estado, estilos[Usuario.ESTADO_PASIVO])
        return format_html(
            '<span style="display:inline-flex; align-items:center; gap:8px; padding:6px 12px; '
            'border-radius:999px; background:{}; color:{}; border:1px solid {}; '
            'font-size:12px; font-weight:700; letter-spacing:0.02em;">'
            '<span style="width:8px; height:8px; border-radius:999px; background:{};"></span>'
            '{}'
            '</span>',
            style['bg'],
            style['text'],
            style['border'],
            style['dot'],
            obj.get_estado_display(),
        )
    estado_badge.short_description = "Estado"

    def editar_usuario(self, obj):
        return format_html(
            '<a href="{}" style="display:inline-flex; align-items:center; justify-content:center; '
            'padding:7px 12px; border-radius:10px; background:#0f172a; color:#fff; '
            'text-decoration:none; font-size:12px; font-weight:700;">Editar</a>',
            reverse('admin:asistencia_usuario_change', args=[obj.pk]),
        )
    editar_usuario.short_description = "Opciones"

    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = Usuario.objects.get(pk=obj.pk)
            before = {
                'estado': old.estado,
            }
        super().save_model(request, obj, form, change)
        after = {
            'estado': obj.estado,
        }
        if change and before != after:
            log_critical_change(
                request.user,
                f'Usuario:{obj.dni}',
                before,
                after,
                action_label='Cambio estado usuario',
            )

@admin.register(Asistencia)
class AsistenciaAdmin(admin.ModelAdmin):
    list_display = (
        'usuario',
        'fecha',
        'hora_ingreso',
        'hora_salida',
        'ubicacion',
        'evento',
        'confirmada_badge',
        'justificada_badge',
        'puntualidad_badge',
        'editar_asistencia',
    )
    search_fields = ('usuario__nombre', 'usuario__apellido', 'usuario__dni')
    list_filter = ('confirmada', 'es_justificada', 'puntualidad', 'ubicacion', 'evento')
    actions = ['confirmar_asistencias']
    list_display_links = ('usuario', 'fecha', 'evento')

    def confirmar_asistencias(self, request, queryset):
        queryset.update(confirmada=True)
        self.message_user(request, "Asistencias confirmadas exitosamente.")
    confirmar_asistencias.short_description = "Confirmar asistencias seleccionadas"

    def confirmada_badge(self, obj):
        if obj.confirmada:
            bg, text, border, dot, label = '#ecfdf5', '#047857', '#a7f3d0', '#10b981', 'Confirmada'
        else:
            bg, text, border, dot, label = '#fff7ed', '#c2410c', '#fdba74', '#f97316', 'Pendiente'
        return format_html(
            '<span style="display:inline-flex; align-items:center; gap:8px; padding:6px 12px; '
            'border-radius:999px; background:{}; color:{}; border:1px solid {}; '
            'font-size:12px; font-weight:700; letter-spacing:0.02em;">'
            '<span style="width:8px; height:8px; border-radius:999px; background:{};"></span>'
            '{}'
            '</span>',
            bg, text, border, dot, label
        )
    confirmada_badge.short_description = "Confirmacion"

    def justificada_badge(self, obj):
        if obj.es_justificada:
            bg, text, border, dot, label = '#ecfeff', '#0f766e', '#a5f3fc', '#06b6d4', 'Justificada'
        else:
            bg, text, border, dot, label = '#f8fafc', '#475569', '#cbd5e1', '#64748b', 'Sin justificar'
        return format_html(
            '<span style="display:inline-flex; align-items:center; gap:8px; padding:6px 12px; '
            'border-radius:999px; background:{}; color:{}; border:1px solid {}; '
            'font-size:12px; font-weight:700; letter-spacing:0.02em;">'
            '<span style="width:8px; height:8px; border-radius:999px; background:{};"></span>'
            '{}'
            '</span>',
            bg, text, border, dot, label
        )
    justificada_badge.short_description = "Justificacion"

    def puntualidad_badge(self, obj):
        estilos = {
            Asistencia.PUNTUALIDAD_PUNTUAL: ('#ecfdf5', '#047857', '#a7f3d0', '#10b981', 'Puntual'),
            Asistencia.PUNTUALIDAD_TARDE: ('#fef2f2', '#b91c1c', '#fecaca', '#ef4444', 'Tarde'),
        }
        bg, text, border, dot, label = estilos.get(
            obj.puntualidad,
            ('#f8fafc', '#475569', '#cbd5e1', '#64748b', obj.puntualidad or 'Sin dato')
        )
        return format_html(
            '<span style="display:inline-flex; align-items:center; gap:8px; padding:6px 12px; '
            'border-radius:999px; background:{}; color:{}; border:1px solid {}; '
            'font-size:12px; font-weight:700; letter-spacing:0.02em;">'
            '<span style="width:8px; height:8px; border-radius:999px; background:{};"></span>'
            '{}'
            '</span>',
            bg, text, border, dot, label
        )
    puntualidad_badge.short_description = "Puntualidad"

    def editar_asistencia(self, obj):
        return format_html(
            '<a href="{}" style="display:inline-flex; align-items:center; justify-content:center; '
            'padding:7px 12px; border-radius:10px; background:#0f172a; color:#fff; '
            'text-decoration:none; font-size:12px; font-weight:700;">Editar</a>',
            reverse('admin:asistencia_asistencia_change', args=[obj.pk]),
        )
    editar_asistencia.short_description = "Opciones"

@admin.register(Ubicacion)
class UbicacionAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'descripcion')
    search_fields = ('nombre',)

@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    form = EventoAdminForm
    list_display = ('nombre', 'fecha', 'hora_ingreso', 'activo_badge', 'editar_evento')
    search_fields = ('nombre',)
    list_filter = ('activo', 'fecha')
    actions = ['finalizar_eventos']
    list_display_links = ('nombre', 'fecha')

    def activo_badge(self, obj):
        if obj.activo:
            bg, text, border, dot, label = '#ecfdf5', '#047857', '#a7f3d0', '#10b981', 'Activo'
        else:
            bg, text, border, dot, label = '#fff7ed', '#c2410c', '#fdba74', '#f97316', 'Finalizado'
        return format_html(
            '<span style="display:inline-flex; align-items:center; gap:8px; padding:6px 12px; '
            'border-radius:999px; background:{}; color:{}; border:1px solid {}; '
            'font-size:12px; font-weight:700; letter-spacing:0.02em;">'
            '<span style="width:8px; height:8px; border-radius:999px; background:{};"></span>'
            '{}'
            '</span>',
            bg, text, border, dot, label
        )
    activo_badge.short_description = "Estado"

    def editar_evento(self, obj):
        return format_html(
            '<a href="{}" style="display:inline-flex; align-items:center; justify-content:center; '
            'padding:7px 12px; border-radius:10px; background:#0f172a; color:#fff; '
            'text-decoration:none; font-size:12px; font-weight:700;">Editar</a>',
            reverse('admin:asistencia_evento_change', args=[obj.pk]),
        )
    editar_evento.short_description = "Opciones"

    def finalizar_eventos(self, request, queryset):
        for evento in queryset:
            before = {'activo': evento.activo}
            evento.activo = False
            evento.save(update_fields=['activo'])
            after = {'activo': evento.activo}
            if before != after:
                log_critical_change(
                    request.user,
                    f'Evento:{evento.id}',
                    before,
                    after,
                    action_label='Cierre manual evento (admin)',
                )
        self.message_user(request, "Eventos finalizados exitosamente.")
    finalizar_eventos.short_description = "Finalizar eventos seleccionados"

    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = Evento.objects.get(pk=obj.pk)
            before = {
                'nombre': old.nombre,
                'fecha': str(old.fecha),
                'hora_ingreso': str(old.hora_ingreso),
                'activo': old.activo,
            }
        super().save_model(request, obj, form, change)
        after = {
            'nombre': obj.nombre,
            'fecha': str(obj.fecha),
            'hora_ingreso': str(obj.hora_ingreso),
            'activo': obj.activo,
        }
        if change and before != after:
            log_critical_change(
                request.user,
                f'Evento:{obj.id}',
                before,
                after,
                action_label='Actualizacion evento',
            )

@admin.register(LogAccion)
class LogAccionAdmin(admin.ModelAdmin):
    list_display = ('usuario_resaltado', 'accion_badge', 'descripcion_corta', 'fecha')
    search_fields = ('usuario__username', 'accion', 'descripcion')
    list_filter = ('accion', 'fecha')
    readonly_fields = ('usuario', 'accion', 'descripcion', 'fecha')
    list_display_links = ('usuario_resaltado', 'fecha')
    ordering = ('-fecha',)

    def usuario_resaltado(self, obj):
        username = obj.usuario.username if obj.usuario else 'Sistema'
        return format_html(
            '<span style="display:inline-flex; align-items:center; padding:6px 10px; '
            'border-radius:999px; background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; '
            'font-size:12px; font-weight:700;">{}</span>',
            username,
        )
    usuario_resaltado.short_description = "Usuario"

    def accion_badge(self, obj):
        accion = (obj.accion or '').lower()
        if 'eliminar' in accion or 'rechaz' in accion:
            bg, text, border = '#fef2f2', '#b91c1c', '#fecaca'
        elif 'actualiz' in accion or 'cambio' in accion or 'edita' in accion:
            bg, text, border = '#eff6ff', '#1d4ed8', '#bfdbfe'
        elif 'aproba' in accion or 'confirm' in accion or 'registr' in accion:
            bg, text, border = '#ecfdf5', '#047857', '#a7f3d0'
        else:
            bg, text, border = '#f8fafc', '#475569', '#cbd5e1'
        return format_html(
            '<span style="display:inline-flex; align-items:center; padding:6px 10px; '
            'border-radius:999px; background:{}; color:{}; border:1px solid {}; '
            'font-size:12px; font-weight:700;">{}</span>',
            bg, text, border, obj.accion
        )
    accion_badge.short_description = "Accion"

    def descripcion_corta(self, obj):
        texto = obj.descripcion or ''
        if len(texto) > 90:
            texto = f"{texto[:87]}..."
        return format_html(
            '<span style="color:#334155; font-size:12px;">{}</span>',
            texto,
        )
    descripcion_corta.short_description = "Descripcion"

@admin.register(ConfiguracionSistema)
class ConfiguracionSistemaAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre_institucion', 'tardanza_activa', 'tolerancia_minutos', 'logo')
    fields = ('nombre_institucion', 'tardanza_activa', 'tolerancia_minutos', 'logo',)

    # Limitar a una sola instancia en la lista
    def has_add_permission(self, request):
        return not ConfiguracionSistema.objects.exists()

@admin.register(Justificacion)
class JustificacionAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'evento', 'estado_badge_justificacion', 'fecha_creacion', 'procesado_por', 'editar_justificacion')
    list_filter = ('estado', 'evento', 'fecha_creacion')
    search_fields = ('usuario__nombre', 'usuario__apellido', 'usuario__dni', 'motivo')
    readonly_fields = ('fecha_creacion', 'procesado_por')
    actions = ['aprobar_justificaciones', 'rechazar_justificaciones']
    list_display_links = ('usuario', 'evento', 'fecha_creacion')

    def estado_badge_justificacion(self, obj):
        estilos = {
            Justificacion.ESTADO_PENDIENTE: ('#fefce8', '#a16207', '#fde68a', '#eab308'),
            Justificacion.ESTADO_APROBADO: ('#ecfdf5', '#047857', '#a7f3d0', '#10b981'),
            Justificacion.ESTADO_RECHAZADO: ('#fef2f2', '#b91c1c', '#fecaca', '#ef4444'),
        }
        bg, text, border, dot = estilos.get(
            obj.estado,
            ('#f8fafc', '#475569', '#cbd5e1', '#64748b')
        )
        return format_html(
            '<span style="display:inline-flex; align-items:center; gap:8px; padding:6px 12px; '
            'border-radius:999px; background:{}; color:{}; border:1px solid {}; '
            'font-size:12px; font-weight:700; letter-spacing:0.02em;">'
            '<span style="width:8px; height:8px; border-radius:999px; background:{};"></span>'
            '{}'
            '</span>',
            bg, text, border, dot, obj.get_estado_display()
        )
    estado_badge_justificacion.short_description = "Estado"

    def editar_justificacion(self, obj):
        return format_html(
            '<a href="{}" style="display:inline-flex; align-items:center; justify-content:center; '
            'padding:7px 12px; border-radius:10px; background:#0f172a; color:#fff; '
            'text-decoration:none; font-size:12px; font-weight:700;">Editar</a>',
            reverse('admin:asistencia_justificacion_change', args=[obj.pk]),
        )
    editar_justificacion.short_description = "Opciones"
    
    def aprobar_justificaciones(self, request, queryset):
        count = 0
        for obj in queryset:
            if obj.estado != Justificacion.ESTADO_APROBADO:
                before = {'estado': obj.estado}
                obj.estado = Justificacion.ESTADO_APROBADO
                obj.procesado_por = request.user
                obj.save()
                log_critical_change(
                    request.user,
                    f'Justificacion:{obj.id}',
                    before,
                    {'estado': obj.estado},
                    action_label='Cambio estado justificacion',
                )
                count += 1
        self.message_user(request, f'{count} justificaciones aprobadas exitosamente.')
    aprobar_justificaciones.short_description = 'Aprobar justificaciones seleccionadas'

    def rechazar_justificaciones(self, request, queryset):
        count = 0
        for obj in queryset:
            if obj.estado != Justificacion.ESTADO_RECHAZADO:
                before = {'estado': obj.estado}
                obj.estado = Justificacion.ESTADO_RECHAZADO
                obj.procesado_por = request.user
                obj.save()
                log_critical_change(
                    request.user,
                    f'Justificacion:{obj.id}',
                    before,
                    {'estado': obj.estado},
                    action_label='Cambio estado justificacion',
                )
                count += 1
        self.message_user(request, f'{count} justificaciones rechazadas.')
    rechazar_justificaciones.short_description = 'Rechazar justificaciones seleccionadas'

    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = Justificacion.objects.get(pk=obj.pk)
            before = {
                'estado': old.estado,
                'comentario_admin': old.comentario_admin,
            }
        if not obj.procesado_por and obj.estado != Justificacion.ESTADO_PENDIENTE:
            obj.procesado_por = request.user
        super().save_model(request, obj, form, change)
        if change:
            after = {
                'estado': obj.estado,
                'comentario_admin': obj.comentario_admin,
            }
            if before != after:
                log_critical_change(
                    request.user,
                    f'Justificacion:{obj.id}',
                    before,
                    after,
                    action_label='Actualizacion justificacion',
                )
