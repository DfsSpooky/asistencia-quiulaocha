from django.contrib import admin
from .models import Usuario, Asistencia, Ubicacion, Evento, LogAccion, ConfiguracionSistema, Justificacion

@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'apellido', 'dni')
    search_fields = ('nombre', 'apellido', 'dni')
    readonly_fields = ('qr_code',)

@admin.register(Asistencia)
class AsistenciaAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'fecha', 'hora_ingreso', 'hora_salida', 'ubicacion', 'evento', 'confirmada', 'es_justificada')
    search_fields = ('usuario__nombre', 'usuario__apellido', 'usuario__dni')
    list_filter = ('confirmada', 'es_justificada', 'ubicacion', 'evento')
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
    list_display = ('nombre', 'fecha', 'activo')
    search_fields = ('nombre',)
    list_filter = ('activo', 'fecha')
    actions = ['finalizar_eventos']

    def finalizar_eventos(self, request, queryset):
        queryset.update(activo=False)
        self.message_user(request, "Eventos finalizados exitosamente.")
    finalizar_eventos.short_description = "Finalizar eventos seleccionados"

@admin.register(LogAccion)
class LogAccionAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'accion', 'fecha')
    search_fields = ('usuario__username', 'accion', 'descripcion')
    list_filter = ('accion', 'fecha')
    readonly_fields = ('usuario', 'accion', 'descripcion', 'fecha')

@admin.register(ConfiguracionSistema)
class ConfiguracionSistemaAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre_institucion', 'logo')
    fields = ('nombre_institucion', 'logo',)

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
                obj.estado = Justificacion.ESTADO_APROBADO
                obj.procesado_por = request.user
                obj.save()
                count += 1
        self.message_user(request, f'{count} justificaciones aprobadas exitosamente.')
    aprobar_justificaciones.short_description = 'Aprobar justificaciones seleccionadas'

    def rechazar_justificaciones(self, request, queryset):
        count = 0
        for obj in queryset:
            if obj.estado != Justificacion.ESTADO_RECHAZADO:
                obj.estado = Justificacion.ESTADO_RECHAZADO
                obj.procesado_por = request.user
                obj.save()
                count += 1
        self.message_user(request, f'{count} justificaciones rechazadas.')
    rechazar_justificaciones.short_description = 'Rechazar justificaciones seleccionadas'

    def save_model(self, request, obj, form, change):
        if not obj.procesado_por and obj.estado != Justificacion.ESTADO_PENDIENTE:
            obj.procesado_por = request.user
        super().save_model(request, obj, form, change)
