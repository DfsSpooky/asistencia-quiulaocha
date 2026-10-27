from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asistencia', '0018_configuracionsistema_avatar_carnet_sin_foto'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuracionsistema',
            name='permitir_carnets_antiguos_sin_firma',
            field=models.BooleanField(
                default=True,
                help_text='Modo rescate / compatibilidad: Acepta carnets físicos impresos previamente aunque la firma digital no coincida con la clave del servidor actual. Recomendado: ACTIVADO para no invalidar carnets ya entregados a los comuneros.',
                verbose_name='Permitir carnets antiguos sin firma',
            ),
        ),
        migrations.AddField(
            model_name='configuracionsistema',
            name='qr_signing_key',
            field=models.CharField(
                blank=True,
                help_text='Clave fija opcional para firmar los nuevos carnets. Si se deja vacía, se usa la variable de entorno QR_SIGNING_KEY o SECRET_KEY.',
                max_length=255,
                null=True,
                verbose_name='Clave de firma QR fija',
            ),
        ),
        migrations.AddField(
            model_name='configuracionsistema',
            name='qr_fallback_keys',
            field=models.TextField(
                blank=True,
                help_text='Claves anteriores separadas por coma para aceptar carnets de versiones o servidores anteriores.',
                null=True,
                verbose_name='Claves QR de respaldo',
            ),
        ),
    ]
