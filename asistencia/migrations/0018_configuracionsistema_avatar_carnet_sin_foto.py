import asistencia.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asistencia', '0017_rename_asistencia__estado_c49725_idx_asistencia__estado_4b510d_idx_and_more'),
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
