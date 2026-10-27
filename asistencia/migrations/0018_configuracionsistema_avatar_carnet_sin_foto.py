import asistencia.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asistencia', '0016_historialcarnet_entrega_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracionsistema',
            name='avatar_carnet_sin_foto',
            field=models.ImageField(
                blank=True,
                help_text='Avatar por defecto solo para imprimir carnets sin foto real. No reemplaza la fotografía del socio en el sistema.',
                null=True,
                upload_to='logos/',
                validators=[asistencia.models.validate_file_size, asistencia.models.validate_image_content],
            ),
        ),
    ]
