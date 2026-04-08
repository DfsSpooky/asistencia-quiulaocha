import os
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from .models import Asistencia, ConfiguracionSistema, Evento, LogAccion, Usuario
from .services import AsistenciaService
from .utils.reports import generate_attendance_csv, get_filtered_attendance_data


class AttendanceEnhancementsTests(TestCase):
    def setUp(self):
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
