from django.contrib import admin
from .models import Usuario, Asistencia, Ubicacion, Evento, LogAccion, ConfiguracionSistema, Justificacion
from .audit import log_critical_change

@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'apellido', 'dni', 'estado')
    search_fields = ('nombre', 'apellido', 'dni')
    readonly_fields = ('qr_code',)

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
    list_display = ('usuario', 'fecha', 'hora_ingreso', 'hora_salida', 'ubicacion', 'evento', 'confirmada', 'es_justificada', 'puntualidad')
    search_fields = ('usuario__nombre', 'usuario__apellido', 'usuario__dni')
    list_filter = ('confirmada', 'es_justificada', 'puntualidad', 'ubicacion', 'evento')
    actions = ['confirmar_asistencias']

    def confirmar_asistencias(self, request, queryset):
        queryset.update(confirmada=True)
        self.message_user(request, "Asistencias confirmadas exitosamente.")
    confirmar_asistencias.short_description = "Confirmar asistencias seleccionadas"

@admin.register(Ubicacion)
class UbicacionAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'descripcion')
    search_fields = ('nombre',)

@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'fecha', 'hora_ingreso', 'activo')
    search_fields = ('nombre',)
    list_filter = ('activo', 'fecha')
    actions = ['finalizar_eventos']

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
    list_display = ('usuario', 'accion', 'fecha')
    search_fields = ('usuario__username', 'accion', 'descripcion')
    list_filter = ('accion', 'fecha')
    readonly_fields = ('usuario', 'accion', 'descripcion', 'fecha')

@admin.register(ConfiguracionSistema)
class ConfiguracionSistemaAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre_institucion', 'tolerancia_minutos', 'logo')
    fields = ('nombre_institucion', 'tolerancia_minutos', 'logo',)

    # Limitar a una sola instancia en la lista
    def has_add_permission(self, request):
        return not ConfiguracionSistema.objects.exists()

@admin.register(Justificacion)
class JustificacionAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'evento', 'estado', 'fecha_creacion', 'procesado_por')
    list_filter = ('estado', 'evento', 'fecha_creacion')
    search_fields = ('usuario__nombre', 'usuario__apellido', 'usuario__dni', 'motivo')
    readonly_fields = ('fecha_creacion', 'procesado_por')
    actions = ['aprobar_justificaciones', 'rechazar_justificaciones']
    
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
