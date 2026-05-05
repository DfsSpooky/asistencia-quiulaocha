import os
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from .models import Asistencia, ConfiguracionSistema, Evento, LogAccion, Usuario
from .services import AsistenciaService


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
