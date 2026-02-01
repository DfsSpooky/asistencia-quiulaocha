from django.db import migrations

def create_scanner_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Usuario = apps.get_model('asistencia', 'Usuario')

    # Obtener el permiso can_scan_qr
    content_type = ContentType.objects.get_for_model(Usuario)
    can_scan_qr = Permission.objects.get(codename='can_scan_qr', content_type=content_type)

    # Crear el grupo Escaneadores
    group, created = Group.objects.get_or_create(name='Escaneadores')
    group.permissions.add(can_scan_qr)

def remove_scanner_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name='Escaneadores').delete()

class Migration(migrations.Migration):

    dependencies = [
        ('asistencia', '0005_configuracionsistema_nombre_institucion'),
    ]

    operations = [
        migrations.RunPython(create_scanner_group, remove_scanner_group),
    ]
