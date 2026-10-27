from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('asistencia', '0015_solicituddescargacarnets'),
    ]

    operations = [
        migrations.AddField(
            model_name='historialcarnet',
            name='entregado_a',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='historialcarnet',
            name='estado_entrega',
            field=models.CharField(
                choices=[
                    ('PENDIENTE_ENTREGA', 'Pendiente de entrega'),
                    ('ENTREGADO', 'Entregado'),
                    ('DEVUELTO_OFICINA', 'Devuelto a oficina'),
                    ('EN_CUSTODIA', 'En custodia'),
                    ('RECOJO_PROGRAMADO', 'Recojo programado'),
                ],
                default='PENDIENTE_ENTREGA',
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name='historialcarnet',
            name='fecha_devolucion',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='historialcarnet',
            name='fecha_entrega',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='historialcarnet',
            name='observaciones_entrega',
            field=models.TextField(blank=True, null=True),
        ),
    ]
