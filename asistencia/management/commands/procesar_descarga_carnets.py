from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from asistencia.models import LogAccion, SolicitudDescargaCarnets
from asistencia.views import _generate_carnets_zip_payload


class Command(BaseCommand):
    help = "Procesa en segundo plano una solicitud de descarga masiva de carnets."

    def add_arguments(self, parser):
        parser.add_argument('solicitud_id', type=int)

    def handle(self, *args, **options):
        solicitud_id = options['solicitud_id']

        try:
            with transaction.atomic():
                solicitud = (
                    SolicitudDescargaCarnets.objects
                    .select_for_update()
                    .select_related('solicitado_por')
                    .get(pk=solicitud_id)
                )
                if solicitud.estado not in {
                    SolicitudDescargaCarnets.ESTADO_PENDIENTE,
                    SolicitudDescargaCarnets.ESTADO_ERROR,
                }:
                    self.stdout.write(
                        self.style.WARNING(
                            f"La solicitud #{solicitud.id} ya está en estado {solicitud.estado}."
                        )
                    )
                    return

                solicitud.estado = SolicitudDescargaCarnets.ESTADO_PROCESANDO
                solicitud.mensaje_error = ''
                solicitud.fecha_inicio = timezone.now()
                solicitud.fecha_fin = None
                solicitud.save(update_fields=['estado', 'mensaje_error', 'fecha_inicio', 'fecha_fin'])
        except SolicitudDescargaCarnets.DoesNotExist as exc:
            raise CommandError(f"No existe la solicitud #{solicitud_id}.") from exc

        try:
            payload = _generate_carnets_zip_payload()
            solicitud.refresh_from_db()
            if solicitud.archivo_zip:
                solicitud.archivo_zip.delete(save=False)
            solicitud.nombre_archivo = payload['zip_filename']
            solicitud.archivo_zip.save(
                payload['zip_filename'],
                ContentFile(payload['zip_bytes']),
                save=False,
            )
            solicitud.estado = SolicitudDescargaCarnets.ESTADO_LISTO
            solicitud.total_carnets_con_foto = payload['total_carnets_con_foto']
            solicitud.total_carnets_sin_foto = payload['total_carnets_sin_foto']
            solicitud.total_usuarios_sin_foto = payload['total_usuarios_sin_foto']
            solicitud.total_incidencias = payload['total_incidencias']
            solicitud.mensaje_error = ''
            solicitud.fecha_fin = timezone.now()
            solicitud.save()

            LogAccion.objects.create(
                usuario=solicitud.solicitado_por,
                accion="Descarga carnets ZIP lista",
                descripcion=(
                    f"Solicitud #{solicitud.id} completada con "
                    f"{payload['total_carnets_con_foto']} carnets con foto, "
                    f"{payload['total_carnets_sin_foto']} carnets sin foto, "
                    f"{payload['total_usuarios_sin_foto']} usuarios en reporte sin foto "
                    f"y {payload['total_incidencias']} incidencias técnicas."
                )
            )
            self.stdout.write(self.style.SUCCESS(f"Solicitud #{solicitud.id} completada."))
        except Exception as exc:
            solicitud.refresh_from_db()
            solicitud.estado = SolicitudDescargaCarnets.ESTADO_ERROR
            solicitud.mensaje_error = str(exc)
            solicitud.fecha_fin = timezone.now()
            solicitud.save(update_fields=['estado', 'mensaje_error', 'fecha_fin'])
            LogAccion.objects.create(
                usuario=solicitud.solicitado_por,
                accion="Descarga carnets ZIP con error",
                descripcion=f"Solicitud #{solicitud.id} falló: {exc}",
            )
            raise
