from django.urls import path
from .views import (
    landing_page, dashboard, lista_usuarios, detalle_usuario, RegistrarAsistencia,
    escanear_qr, historial_asistencias, descargar_reporte_pdf,
    importar_usuarios, perfil_usuario, descargar_reporte_evento_pdf,
    registrar_usuario, confirmar_asistencia, exportar_asistencias_csv,
    exportar_asistencias_excel, keep_alive, descargar_reporte_usuario_pdf, 
    solicitar_justificacion, admin_solicitar_justificacion,
    ListEventosActivos, ListUbicaciones, ListDentroEvento, buscar_usuario_dni, SystemConfigView
)
from rest_framework.authtoken.views import obtain_auth_token

urlpatterns = [
    path('', landing_page, name='landing_page'),
    path('dashboard/', dashboard, name='dashboard'),
    path('lista_usuarios/', lista_usuarios, name='lista_usuarios'),
    path('usuario/<str:dni>/', detalle_usuario, name='detalle_usuario'),
    path('api/registrar-asistencia/', RegistrarAsistencia.as_view(), name='registrar_asistencia'),
    path('api/login/', obtain_auth_token, name='api_token_auth'),
    path('api/eventos-activos/', ListEventosActivos.as_view(), name='api_eventos_activos'),
    path('api/ubicaciones/', ListUbicaciones.as_view(), name='api_ubicaciones'),
    path('api/dentro-evento/<int:evento_id>/', ListDentroEvento.as_view(), name='api_dentro_evento'),
    path('api/config-sistema/', SystemConfigView.as_view(), name='api_config_sistema'),
    path('escanear/', escanear_qr, name='escanear_qr'),
    path('escanear/<int:evento_id>/', escanear_qr, name='escanear_qr_evento'),
    path('keep-alive/', keep_alive, name='keep_alive'),
    path('historial/', historial_asistencias, name='historial_asistencias'),
    path('descargar-reporte/', descargar_reporte_pdf, name='descargar_reporte_pdf'),
    path('exportar-asistencias-csv/', exportar_asistencias_csv, name='exportar_asistencias_csv'),
    path('exportar-asistencias-excel/', exportar_asistencias_excel, name='exportar_asistencias_excel'),
    path('importar-usuarios/', importar_usuarios, name='importar_usuarios'),
    path('perfil/', perfil_usuario, name='perfil_usuario'),
    path('solicitar_justificacion/', solicitar_justificacion, name='solicitar_justificacion'),
    path('admin-justificar/', admin_solicitar_justificacion, name='admin_solicitar_justificacion'),
    path('descargar-reporte-evento/<int:evento_id>/', descargar_reporte_evento_pdf, name='descargar_reporte_evento_pdf'),
    path('registrar-usuario/', registrar_usuario, name='registrar_usuario'),
    path('confirmar-asistencia/<int:asistencia_id>/', confirmar_asistencia, name='confirmar_asistencia'),
    path('descargar-reporte-usuario/<str:dni>/', descargar_reporte_usuario_pdf, name='descargar_reporte_usuario_pdf'),  # Nueva ruta
    path('api/buscar-usuario-dni/', buscar_usuario_dni, name='buscar_usuario_dni'),
]