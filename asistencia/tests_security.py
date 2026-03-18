from django.test import TestCase, Client
from django.contrib.auth.models import User
from asistencia.models import Usuario
from asistencia.views import safe_extract
import tarfile
import os
from unittest.mock import MagicMock
from django.urls import reverse

class SecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='admin', password='password', is_staff=True, is_superuser=True)
        self.client = Client()
        self.client.force_login(self.user)

    def test_xss_buscar_usuario_dni(self):
        # Create user with safe name first
        u = Usuario.objects.create(
            nombre='Safe',
            apellido='Evil',
            dni='12345678',
            user=self.user
        )
        # Bypass validation using update() to inject malicious content
        Usuario.objects.filter(pk=u.pk).update(nombre='<script>alert(1)</script>')

        # Test search
        response = self.client.get('/api/buscar-usuario-dni/', {'dni': '12345678'})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # Ensure script tags are escaped
        self.assertNotIn('<script>', content)
        self.assertIn('&lt;script&gt;', content)
        self.assertIn('Evil', content)

    def test_zip_slip_prevention(self):
        # Create a mock TarInfo with a path traversal attempt
        mock_member = MagicMock(spec=tarfile.TarInfo)
        mock_member.name = '../evil_file.txt'
        mock_member.issym.return_value = False
        mock_member.islnk.return_value = False

        mock_tar = MagicMock()
        mock_tar.getmembers.return_value = [mock_member]

        # Call safe_extract and expect exception
        with self.assertRaises(Exception) as cm:
            safe_extract(mock_tar, path='/tmp/restore')

        self.assertIn("Intento de Path Traversal detectado", str(cm.exception))

    def test_backup_extract_rejects_symlinks(self):
        mock_member = MagicMock(spec=tarfile.TarInfo)
        mock_member.name = 'media/link'
        mock_member.issym.return_value = True
        mock_member.islnk.return_value = False

        mock_tar = MagicMock()
        mock_tar.getmembers.return_value = [mock_member]

        with self.assertRaises(Exception) as cm:
            safe_extract(mock_tar, path='/tmp/restore')

        self.assertIn("No se permiten enlaces", str(cm.exception))

    def test_admin_url_change(self):
        # Check that old admin URL is gone (should return 404 or redirect to login depending on config, but likely 404 if not matched)
        # Actually, admin urls are not loaded at root if include is used properly.
        # But 'admin/' is gone from urlpatterns.
        response = self.client.get('/admin/')
        # Should be 404 because pattern doesn't exist anymore
        self.assertEqual(response.status_code, 404)

        # Check new admin URL
        response = self.client.get('/gestionsegura/')
        # Should be 302 to login because not logged in as superuser (wait, I am logged in as superuser)
        # If logged in as superuser, should be 200 or 302 depending on admin index redirection.
        # Admin index usually redirects to login if not authenticated or shows index if authenticated.
        # Since I am logged in as superuser, it should return 200 (admin index).
        self.assertEqual(response.status_code, 200)

    def test_reporte_global_requires_manage_permission(self):
        user = User.objects.create_user(username='normal', password='password')
        self.client.force_login(user)
        response = self.client.get('/descargar-reporte-global/')
        self.assertEqual(response.status_code, 403)

    def test_logout_requires_post(self):
        response = self.client.get('/logout/')
        self.assertEqual(response.status_code, 405)
