import uuid

from django.db import migrations, models


def populate_unique_qr_uid(apps, schema_editor):
    Usuario = apps.get_model('asistencia', 'Usuario')

    used = set()
    for usuario in Usuario.objects.all().iterator():
        current = str(usuario.qr_uid) if usuario.qr_uid else None
        if current and current not in used:
            used.add(current)
            continue

        new_uid = str(uuid.uuid4())
        while new_uid in used:
            new_uid = str(uuid.uuid4())

        usuario.qr_uid = new_uid
        usuario.save(update_fields=['qr_uid'])
        used.add(new_uid)


class Migration(migrations.Migration):

    dependencies = [
        ('asistencia', '0013_alter_historialcarnet_fecha_emision'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuario',
            name='qr_uid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.RunPython(populate_unique_qr_uid, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='usuario',
            name='qr_uid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name='usuario',
            name='qr_version',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
