from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('asistencia', '0014_usuario_qr_uid_qr_version'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SolicitudDescargaCarnets',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('estado', models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('PROCESANDO', 'Procesando'), ('LISTO', 'Listo'), ('ERROR', 'Error')], default='PENDIENTE', max_length=12)),
                ('archivo_zip', models.FileField(blank=True, null=True, upload_to='descargas_carnets/')),
                ('nombre_archivo', models.CharField(blank=True, max_length=255)),
                ('total_carnets_con_foto', models.PositiveIntegerField(default=0)),
                ('total_carnets_sin_foto', models.PositiveIntegerField(default=0)),
                ('total_usuarios_sin_foto', models.PositiveIntegerField(default=0)),
                ('total_incidencias', models.PositiveIntegerField(default=0)),
                ('mensaje_error', models.TextField(blank=True)),
                ('fecha_solicitud', models.DateTimeField(auto_now_add=True)),
                ('fecha_inicio', models.DateTimeField(blank=True, null=True)),
                ('fecha_fin', models.DateTimeField(blank=True, null=True)),
                ('solicitado_por', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='solicitudes_descarga_carnets', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-fecha_solicitud'],
            },
        ),
        migrations.AddIndex(
            model_name='solicituddescargacarnets',
            index=models.Index(fields=['estado'], name='asistencia__estado_c49725_idx'),
        ),
        migrations.AddIndex(
            model_name='solicituddescargacarnets',
            index=models.Index(fields=['solicitado_por', 'fecha_solicitud'], name='asistencia__solicit_f8f229_idx'),
        ),
    ]
