from django.db import migrations

def create_scanner_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    # Crear el grupo Escaneadores
    Group.objects.get_or_create(name='Escaneadores')

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
