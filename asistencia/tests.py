import os
import shutil
import sys
import tempfile
import types
import zipfile
from datetime import date, datetime, time, timedelta
from io import BytesIO
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import Asistencia, ConfiguracionSistema, Evento, HistorialCarnet, Justificacion, LogAccion, Usuario
from .services import AsistenciaService
from .forms import FiltroAsistenciaForm
from .utils.reports import generate_attendance_csv, get_filtered_attendance_data


class AttendanceEnhancementsTests(TestCase):
    def setUp(self):
        self.temp_media_root = tempfile.mkdtemp(prefix='test-media-')
        self._original_media_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self.temp_media_root

        self.user = User.objects.create_user(username='manager', password='password')
        self.user.user_permissions.add(
            Permission.objects.get(codename='can_manage_users'),
            Permission.objects.get(codename='can_scan_qr'),
        )
        self.client = Client()
        self.client.force_login(self.user)

        ConfiguracionSistema.objects.create(nombre_institucion='QUIULACOCHA', tolerancia_minutos=15)
        self.evento = Evento.objects.create(
            nombre='Asamblea General',
            fecha=date.today(),
            hora_ingreso=time(8, 0),
            activo=True,
        )

    def tearDown(self):
        settings.MEDIA_ROOT = self._original_media_root
        shutil.rmtree(self.temp_media_root, ignore_errors=True)

    def test_registrar_ingreso_marca_puntualidad(self):
        socio_puntual = Usuario.objects.create(nombre='Ana', apellido='Perez', dni='12345670', estado=Usuario.ESTADO_ACTIVO)
        socio_tarde = Usuario.objects.create(nombre='Luis', apellido='Rojas', dni='12345671', estado=Usuario.ESTADO_ACTIVO)

        ts_puntual = timezone.make_aware(datetime.combine(date.today(), time(8, 10)))
        ts_tarde = timezone.make_aware(datetime.combine(date.today(), time(8, 30)))

        a1, _, _ = AsistenciaService.registrar_ingreso(
            usuario=socio_puntual,
            evento=self.evento,
            ubicacion=None,
            request_user=self.user,
            fecha_registro=ts_puntual,
        )
        a2, _, _ = AsistenciaService.registrar_ingreso(
            usuario=socio_tarde,
            evento=self.evento,
            ubicacion=None,
            request_user=self.user,
            fecha_registro=ts_tarde,
        )

        self.assertEqual(a1.puntualidad, Asistencia.PUNTUALIDAD_PUNTUAL)
        self.assertEqual(a2.puntualidad, Asistencia.PUNTUALIDAD_TARDE)

    def test_tardanza_activa_convierte_tarde_en_falta_en_reportes(self):
        socio_tarde = Usuario.objects.create(nombre='Luis', apellido='Rojas', dni='12345671', estado=Usuario.ESTADO_ACTIVO)
        ts_tarde = timezone.make_aware(datetime.combine(date.today(), time(8, 30)))

        AsistenciaService.registrar_ingreso(
            usuario=socio_tarde,
            evento=self.evento,
            ubicacion=None,
            request_user=self.user,
            fecha_registro=ts_tarde,
        )

        data = get_filtered_attendance_data({'evento': self.evento})
        unified_list = data['unified_report']

        self.assertEqual(len(unified_list), 1)
        self.assertTrue(unified_list[0].is_absent)
        self.assertTrue(unified_list[0].is_late_absent)

        csv_response = generate_attendance_csv(unified_list)
        csv_content = csv_response.content.decode('utf-8')

        self.assertIn('FALTA', csv_content)
        self.assertIn('Tardanza', csv_content)

    def test_tardanza_desactivada_mantiene_registro_como_presente(self):
        config = ConfiguracionSistema.objects.get()
        config.tardanza_activa = False
        config.save(update_fields=['tardanza_activa'])

        socio_tarde = Usuario.objects.create(nombre='Luis', apellido='Rojas', dni='12345671', estado=Usuario.ESTADO_ACTIVO)
        ts_tarde = timezone.make_aware(datetime.combine(date.today(), time(8, 30)))

        AsistenciaService.registrar_ingreso(
            usuario=socio_tarde,
            evento=self.evento,
            ubicacion=None,
            request_user=self.user,
            fecha_registro=ts_tarde,
        )

        data = get_filtered_attendance_data({'evento': self.evento})
        unified_list = data['unified_report']

        self.assertEqual(len(unified_list), 1)
        self.assertFalse(unified_list[0].is_absent)
        self.assertFalse(getattr(unified_list[0], 'is_late_absent', False))
        self.assertEqual(unified_list[0].puntualidad, Asistencia.PUNTUALIDAD_TARDE)

    def test_exonerado_tarde_no_se_convierte_en_falta(self):
        socio_exonerado = Usuario.objects.create(
            nombre='Julia',
            apellido='Diaz',
            dni='12345699',
            estado=Usuario.ESTADO_EXONERADO,
        )
        Asistencia.objects.create(
            usuario=socio_exonerado,
            evento=self.evento,
            fecha=self.evento.fecha,
            hora_ingreso=time(8, 30),
            confirmada=True,
            puntualidad=Asistencia.PUNTUALIDAD_TARDE,
        )

        data = get_filtered_attendance_data({'evento': self.evento})
        unified_list = data['unified_report']

        self.assertEqual(len(unified_list), 1)
        self.assertFalse(unified_list[0].is_absent)
        self.assertFalse(getattr(unified_list[0], 'is_late_absent', False))

    def test_filtro_faltaron_ignora_confirmada_para_ausencias(self):
        socio_tarde = Usuario.objects.create(nombre='Luis', apellido='Rojas', dni='12345671', estado=Usuario.ESTADO_ACTIVO)
        socio_justificado = Usuario.objects.create(nombre='Marta', apellido='Lopez', dni='12345672', estado=Usuario.ESTADO_ACTIVO)
        socio_sin_registro = Usuario.objects.create(nombre='Jose', apellido='Diaz', dni='12345673', estado=Usuario.ESTADO_ACTIVO)

        ts_tarde = timezone.make_aware(datetime.combine(date.today(), time(8, 30)))
        AsistenciaService.registrar_ingreso(
            usuario=socio_tarde,
            evento=self.evento,
            ubicacion=None,
            request_user=self.user,
            fecha_registro=ts_tarde,
        )

        Asistencia.objects.create(
            usuario=socio_justificado,
            evento=self.evento,
            fecha=self.evento.fecha,
            confirmada=True,
            es_justificada=True,
        )

        data_sin_confirmada = get_filtered_attendance_data({'evento': self.evento, 'estado': 'faltaron'})
        data_confirmada_true = get_filtered_attendance_data({'evento': self.evento, 'estado': 'faltaron', 'confirmada': 'true'})
        data_confirmada_false = get_filtered_attendance_data({'evento': self.evento, 'estado': 'faltaron', 'confirmada': 'false'})

        def resumen(data):
            return sorted(
                (
                    item.usuario.dni if hasattr(item, 'usuario') else item.dni,
                    bool(getattr(item, 'is_late_absent', False)),
                    bool(getattr(item, 'es_justificada', False)),
                )
                for item in data['unified_report']
            )

        esperado = resumen(data_sin_confirmada)
        self.assertIn((socio_tarde.dni, True, False), esperado)
        self.assertIn((socio_justificado.dni, False, True), esperado)
        self.assertIn((socio_sin_registro.dni, False, False), esperado)
        self.assertEqual(esperado, resumen(data_confirmada_true))
        self.assertEqual(esperado, resumen(data_confirmada_false))

    def test_cerrar_evento_lo_inactiva_y_genera_resumen(self):
        response = self.client.post(reverse('cerrar_evento', args=[self.evento.id]))
        self.assertEqual(response.status_code, 200)

        self.evento.refresh_from_db()
        self.assertFalse(self.evento.activo)

        payload = response.json()
        self.assertIn('pdf_url', payload)
        self.assertIn('excel_url', payload)

        pdf_name = os.path.basename(payload['pdf_url'])
        excel_name = os.path.basename(payload['excel_url'])
        self.assertTrue(os.path.exists(os.path.join(settings.MEDIA_ROOT, 'cierres_evento', pdf_name)))
        self.assertTrue(os.path.exists(os.path.join(settings.MEDIA_ROOT, 'cierres_evento', excel_name)))

        audit_log = LogAccion.objects.filter(accion__icontains='Auditoria').order_by('-fecha').first()
        self.assertIsNotNone(audit_log)

    def test_cerrar_evento_bloquea_si_hay_inconsistencia_exonerados(self):
        Usuario.objects.create(
            nombre='Exo',
            apellido='SinRegistro',
            dni='12345999',
            estado=Usuario.ESTADO_EXONERADO,
        )

        response = self.client.post(reverse('cerrar_evento', args=[self.evento.id]))
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertEqual(payload.get('code'), 'EXONERADO_MISMATCH')

        self.evento.refresh_from_db()
        self.assertTrue(self.evento.activo)

    def test_actualizar_foto_rapida_sube_imagen_y_devuelve_fragmento_htmx(self):
        usuario = Usuario.objects.create(nombre='Elena', apellido='Quispe', dni='12345674', estado=Usuario.ESTADO_ACTIVO)
        source = BytesIO()
        Image.new('RGB', (1200, 900), color=(24, 120, 196)).save(source, format='PNG')
        imagen = SimpleUploadedFile('avatar.png', source.getvalue(), content_type='image/png')

        response = self.client.post(
            reverse('actualizar_foto_rapida', args=[usuario.dni]),
            {'foto': imagen},
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        usuario.refresh_from_db()
        self.assertTrue(bool(usuario.foto_perfil))
        self.assertTrue(usuario.foto_perfil.name.endswith('.jpg'))

        with Image.open(usuario.foto_perfil.path) as stored_image:
            self.assertEqual(stored_image.size, Usuario.FOTO_PERFIL_SIZE)
            self.assertEqual(stored_image.format, 'JPEG')

        content = response.content.decode()
        self.assertIn(f'id="avatar-container-{usuario.dni}"', content)
        self.assertIn('hx-trigger="change"', content)
        self.assertIn('bi-camera-fill', content)
        self.assertTrue(
            LogAccion.objects.filter(
                usuario=self.user,
                accion='Actualización de foto rápida',
            ).exists()
        )

    def test_actualizar_foto_rapida_requiere_permiso(self):
        usuario = Usuario.objects.create(nombre='Mario', apellido='Lopez', dni='12345675', estado=Usuario.ESTADO_ACTIVO)
        no_manager = User.objects.create_user(username='viewer', password='password')
        self.client.force_login(no_manager)

        response = self.client.post(reverse('actualizar_foto_rapida', args=[usuario.dni]))

        self.assertEqual(response.status_code, 403)

    def test_filtro_asistencia_rechaza_rango_invalido(self):
        form = FiltroAsistenciaForm(data={
            'fecha_inicio': '2026-04-10',
            'fecha_fin': '2026-04-01',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('La fecha inicio no puede ser mayor que la fecha fin.', form.errors['__all__'])

    def test_exportar_excel_respeta_filtro_confirmada(self):
        usuario_confirmado = Usuario.objects.create(nombre='Rosa', apellido='Diaz', dni='12345676', estado=Usuario.ESTADO_ACTIVO)
        usuario_pendiente = Usuario.objects.create(nombre='Saul', apellido='Perez', dni='12345677', estado=Usuario.ESTADO_ACTIVO)

        Asistencia.objects.create(
            usuario=usuario_confirmado,
            fecha=self.evento.fecha,
            hora_ingreso=time(8, 5),
            hora_salida=time(10, 0),
            evento=self.evento,
            confirmada=True,
        )
        Asistencia.objects.create(
            usuario=usuario_pendiente,
            fecha=self.evento.fecha,
            hora_ingreso=time(8, 10),
            evento=self.evento,
            confirmada=False,
        )

        response = self.client.get(reverse('exportar_asistencias_excel'), {
            'evento': self.evento.id,
            'confirmada': 'true',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            response['Content-Type']
        )

    def test_historial_con_filtro_avanzado_por_fecha_muestra_resultados(self):
        evento_marzo = Evento.objects.create(
            nombre='Reunion Marzo',
            fecha=date(2026, 3, 15),
            hora_ingreso=time(8, 0),
            activo=False,
        )
        evento_abril = Evento.objects.create(
            nombre='Reunion Abril',
            fecha=date(2026, 4, 2),
            hora_ingreso=time(8, 0),
            activo=False,
        )
        usuario = Usuario.objects.create(nombre='Julia', apellido='Soto', dni='12345679', estado=Usuario.ESTADO_ACTIVO)

        Asistencia.objects.create(
            usuario=usuario,
            fecha=evento_marzo.fecha,
            hora_ingreso=time(8, 5),
            hora_salida=time(10, 0),
            evento=evento_marzo,
            confirmada=True,
        )
        Asistencia.objects.create(
            usuario=usuario,
            fecha=evento_abril.fecha,
            hora_ingreso=time(8, 10),
            hora_salida=time(10, 10),
            evento=evento_abril,
            confirmada=True,
        )

        response = self.client.get(reverse('historial_asistencias'), {
            'fecha_inicio': '2026-03-01',
            'fecha_fin': '2026-04-30',
        })

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Reunion Marzo', content)
        self.assertIn('Reunion Abril', content)

    def test_reporte_filtrado_pdf_indica_varios_eventos_en_rango(self):
        evento_1 = Evento.objects.create(
            nombre='Evento Febrero',
            fecha=date(2026, 2, 10),
            hora_ingreso=time(8, 0),
            activo=False,
        )
        evento_2 = Evento.objects.create(
            nombre='Evento Marzo',
            fecha=date(2026, 3, 5),
            hora_ingreso=time(8, 0),
            activo=False,
        )
        usuario = Usuario.objects.create(nombre='Pablo', apellido='Rios', dni='12345680', estado=Usuario.ESTADO_ACTIVO)

        Asistencia.objects.create(
            usuario=usuario,
            fecha=evento_1.fecha,
            hora_ingreso=time(8, 0),
            hora_salida=time(10, 0),
            evento=evento_1,
            confirmada=True,
        )
        Asistencia.objects.create(
            usuario=usuario,
            fecha=evento_2.fecha,
            hora_ingreso=time(8, 15),
            hora_salida=time(10, 15),
            evento=evento_2,
            confirmada=True,
        )

        response = self.client.get(reverse('descargar_reporte_filtrado_pdf'), {
            'fecha_inicio': '2026-02-01',
            'fecha_fin': '2026-03-31',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_filtro_todos_en_rango_incluye_asistencias_y_faltas(self):
        evento_1 = Evento.objects.create(
            nombre='Evento Uno',
            fecha=date(2026, 3, 10),
            hora_ingreso=time(8, 0),
            activo=False,
        )
        evento_2 = Evento.objects.create(
            nombre='Evento Dos',
            fecha=date(2026, 4, 10),
            hora_ingreso=time(8, 0),
            activo=False,
        )
        usuario_asiste = Usuario.objects.create(nombre='Lucia', apellido='Torres', dni='12345681', estado=Usuario.ESTADO_ACTIVO)
        usuario_falta = Usuario.objects.create(nombre='Marco', apellido='Vega', dni='12345682', estado=Usuario.ESTADO_ACTIVO)

        Asistencia.objects.create(
            usuario=usuario_asiste,
            fecha=evento_1.fecha,
            hora_ingreso=time(8, 0),
            hora_salida=time(10, 0),
            evento=evento_1,
            confirmada=True,
        )

        data = get_filtered_attendance_data({
            'fecha_inicio': date(2026, 3, 1),
            'fecha_fin': date(2026, 4, 30),
            'estado': '',
        })

        resumen = sorted(
            (
                item.usuario.dni if hasattr(item, 'usuario') else item.dni,
                getattr(item.evento, 'nombre', None),
                bool(getattr(item, 'is_absent', False)),
            )
            for item in data['unified_report']
        )

        self.assertIn((usuario_asiste.dni, 'Evento Uno', False), resumen)
        self.assertIn((usuario_falta.dni, 'Evento Uno', True), resumen)
        self.assertIn((usuario_asiste.dni, 'Evento Dos', True), resumen)
        self.assertIn((usuario_falta.dni, 'Evento Dos', True), resumen)

    def test_justificacion_rechazada_vuelve_a_falta_en_reportes(self):
        usuario = Usuario.objects.create(
            nombre='Nora',
            apellido='Quispe',
            dni='12345683',
            estado=Usuario.ESTADO_ACTIVO,
        )

        justificacion = Justificacion.objects.create(
            usuario=usuario,
            evento=self.evento,
            motivo='Motivo inicial',
            estado=Justificacion.ESTADO_APROBADO,
            procesado_por=self.user,
        )

        self.assertTrue(
            Asistencia.objects.filter(usuario=usuario, evento=self.evento, es_justificada=True).exists()
        )

        justificacion.estado = Justificacion.ESTADO_RECHAZADO
        justificacion.save()

        self.assertFalse(Asistencia.objects.filter(usuario=usuario, evento=self.evento).exists())

        data = get_filtered_attendance_data({'evento': self.evento, 'estado': 'faltas_injustificadas'})
        resumen = {
            (
                item.usuario.dni if hasattr(item, 'usuario') else item.dni,
                bool(getattr(item, 'is_absent', False)),
                bool(getattr(item, 'es_justificada', False)),
            )
            for item in data['unified_report']
        }
        self.assertIn((usuario.dni, True, False), resumen)

    def test_admin_no_puede_justificar_usuario_exonerado(self):
        usuario_exonerado = Usuario.objects.create(
            nombre='Elio',
            apellido='Exonerado',
            dni='12345684',
            estado=Usuario.ESTADO_EXONERADO,
        )

        response = self.client.post(
            reverse('admin_solicitar_justificacion'),
            data={
                'usuario': usuario_exonerado.id,
                'evento': self.evento.id,
                'motivo': 'Intento de justificación inválido',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Este usuario es exonerado y no se puede proceder con una justificación.',
        )
        self.assertFalse(
            Justificacion.objects.filter(usuario=usuario_exonerado, evento=self.evento).exists()
        )


class CarnetsZipDownloadTests(TestCase):
    def setUp(self):
        self.temp_media_root = tempfile.mkdtemp(prefix='test-media-')
        self._original_media_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self.temp_media_root

        self.user = User.objects.create_user(username='manager_zip', password='password')
        self.user.user_permissions.add(Permission.objects.get(codename='can_manage_users'))
        self.client = Client()
        self.client.force_login(self.user)

        ConfiguracionSistema.objects.create(nombre_institucion='QUIULACOCHA', tolerancia_minutos=15)

    def tearDown(self):
        settings.MEDIA_ROOT = self._original_media_root
        shutil.rmtree(self.temp_media_root, ignore_errors=True)

    def _create_photo_file(self, color=(24, 120, 196)):
        source = BytesIO()
        Image.new('RGB', (500, 500), color=color).save(source, format='PNG')
        return SimpleUploadedFile('avatar.png', source.getvalue(), content_type='image/png')

    def _build_fake_pdf_modules(self):
        class FakeHTML:
            def __init__(self, string):
                self.string = string

            def write_pdf(self):
                if 'incidencias' in self.string.lower():
                    return b'INCIDENTES_PDF'
                return b'CARNETS_PDF'

        class FakePdfReader:
            def __init__(self, buffer):
                self.pages = [buffer.getvalue()]

        class FakePdfWriter:
            def __init__(self):
                self.pages = []

            def add_page(self, page):
                self.pages.append(page)

            def write(self, buffer):
                buffer.write(b''.join(self.pages) or b'PDF_VACIO')

        return (
            types.SimpleNamespace(HTML=FakeHTML),
            types.SimpleNamespace(PdfReader=FakePdfReader, PdfWriter=FakePdfWriter),
        )

    def test_descarga_zip_mixta_incluye_carnets_y_reporte_de_incidencias(self):
        Usuario.objects.create(
            nombre='Ana',
            apellido='ConFoto',
            dni='12345690',
            estado=Usuario.ESTADO_ACTIVO,
            foto_perfil=self._create_photo_file(),
        )
        Usuario.objects.create(
            nombre='Luis',
            apellido='SinFoto',
            dni='12345691',
            estado=Usuario.ESTADO_PASIVO,
        )

        fake_weasyprint, fake_pypdf = self._build_fake_pdf_modules()
        with mock.patch.dict(sys.modules, {'weasyprint': fake_weasyprint, 'pypdf': fake_pypdf}):
            response = self.client.get(reverse('descargar_todos_carnets_pdf'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('carnets_y_pendientes_', response['Content-Disposition'])

        with zipfile.ZipFile(BytesIO(response.content)) as zip_file:
            self.assertEqual(
                sorted(zip_file.namelist()),
                ['carnets_generados.pdf', 'usuarios_sin_foto_notificar.pdf'],
            )
            self.assertEqual(zip_file.read('carnets_generados.pdf'), b'CARNETS_PDF')
            self.assertEqual(zip_file.read('usuarios_sin_foto_notificar.pdf'), b'INCIDENTES_PDF')

    def test_descarga_zip_solo_con_foto_genera_unico_pdf(self):
        Usuario.objects.create(
            nombre='Rosa',
            apellido='Valida',
            dni='12345692',
            estado=Usuario.ESTADO_ACTIVO,
            foto_perfil=self._create_photo_file(color=(0, 180, 90)),
        )

        fake_weasyprint, fake_pypdf = self._build_fake_pdf_modules()
        with mock.patch.dict(sys.modules, {'weasyprint': fake_weasyprint, 'pypdf': fake_pypdf}):
            response = self.client.get(reverse('descargar_todos_carnets_pdf'))

        with zipfile.ZipFile(BytesIO(response.content)) as zip_file:
            self.assertEqual(zip_file.namelist(), ['carnets_generados.pdf'])

    def test_descarga_zip_reporta_archivo_foto_faltante_como_incidencia(self):
        usuario = Usuario.objects.create(
            nombre='Mario',
            apellido='ArchivoPerdido',
            dni='12345693',
            estado=Usuario.ESTADO_ACTIVO,
            foto_perfil=self._create_photo_file(color=(180, 60, 60)),
        )
        os.remove(usuario.foto_perfil.path)

        fake_weasyprint, fake_pypdf = self._build_fake_pdf_modules()
        with mock.patch.dict(sys.modules, {'weasyprint': fake_weasyprint, 'pypdf': fake_pypdf}):
            response = self.client.get(reverse('descargar_todos_carnets_pdf'))

        with zipfile.ZipFile(BytesIO(response.content)) as zip_file:
            self.assertEqual(zip_file.namelist(), ['usuarios_sin_foto_notificar.pdf'])


class GestionCarnetsModuleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='manager_carnets', password='password')
        self.user.user_permissions.add(Permission.objects.get(codename='can_manage_users'))
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse('modulo_gestion_carnets')

    def _create_photo_file(self, name='foto.jpg', color=(40, 100, 180)):
        image = Image.new('RGB', (50, 50), color=color)
        buffer = BytesIO()
        image.save(buffer, format='JPEG')
        return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')

    def test_reposicion_perdida_guarda_motivo_estandar_y_auditoria(self):
        usuario = Usuario.objects.create(
            nombre='Lucia',
            apellido='Prueba',
            dni='76543210',
            estado=Usuario.ESTADO_ACTIVO,
            foto_perfil=self._create_photo_file(),
        )
        version_inicial = usuario.qr_version
        HistorialCarnet.objects.create(
            usuario=usuario,
            motivo='Primer Carnet',
            estado='Activo',
            entregado_por=self.user,
        )

        response = self.client.post(
            self.url,
            {'usuario_id': usuario.id, 'accion': 'reposicion_perdida'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            HistorialCarnet.objects.filter(
                usuario=usuario,
                motivo='Reposición por Pérdida/Robo',
                estado='Activo',
                entregado_por=self.user,
            ).exists()
        )
        self.assertTrue(
            LogAccion.objects.filter(
                usuario=self.user,
                accion='Emisión de carnet físico',
                descripcion__contains='Reposición por Pérdida/Robo',
            ).exists()
        )
        usuario.refresh_from_db()
        self.assertEqual(usuario.qr_version, version_inicial + 1)

    def test_primer_carnet_requiere_foto(self):
        usuario = Usuario.objects.create(
            nombre='Mario',
            apellido='SinFoto',
            dni='76543211',
            estado=Usuario.ESTADO_ACTIVO,
        )

        response = self.client.post(
            self.url,
            {'usuario_id': usuario.id, 'accion': 'primer_carnet'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(HistorialCarnet.objects.filter(usuario=usuario).exists())
        mensajes = [str(m) for m in response.context['messages']]
        self.assertTrue(any('sin fotografía' in mensaje for mensaje in mensajes))

    def test_primer_carnet_no_permite_duplicar_activo(self):
        usuario = Usuario.objects.create(
            nombre='Ana',
            apellido='Activa',
            dni='76543212',
            estado=Usuario.ESTADO_ACTIVO,
            foto_perfil=self._create_photo_file(name='activa.jpg', color=(120, 120, 20)),
        )
        HistorialCarnet.objects.create(
            usuario=usuario,
            motivo='Primer Carnet',
            estado='Activo',
            entregado_por=self.user,
        )

        response = self.client.post(
            self.url,
            {'usuario_id': usuario.id, 'accion': 'primer_carnet'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(HistorialCarnet.objects.filter(usuario=usuario, estado='Activo').count(), 1)
        mensajes = [str(m) for m in response.context['messages']]
        self.assertTrue(any('ya cuenta con un carnet activo' in mensaje for mensaje in mensajes))
