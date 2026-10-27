from django.db import migrations, models


def populate_puntualidad(apps, schema_editor):
    Asistencia = apps.get_model("asistencia", "Asistencia")
    Evento = apps.get_model("asistencia", "Evento")
    ConfiguracionSistema = apps.get_model("asistencia", "ConfiguracionSistema")
    from datetime import datetime, timedelta

    config = ConfiguracionSistema.objects.first()
    tolerancia = config.tolerancia_minutos if config else 15

    for asistencia in Asistencia.objects.filter(hora_ingreso__isnull=False).select_related("evento"):
        if not asistencia.evento_id:
            asistencia.puntualidad = "NO_APLICA"
            asistencia.save(update_fields=["puntualidad"])
            continue

        evento = asistencia.evento
        hora_limite = datetime.combine(asistencia.fecha, evento.hora_ingreso) + timedelta(minutes=tolerancia)
        ingreso = datetime.combine(asistencia.fecha, asistencia.hora_ingreso)
        asistencia.puntualidad = "PUNTUAL" if ingreso <= hora_limite else "TARDE"
        asistencia.save(update_fields=["puntualidad"])


class Migration(migrations.Migration):
    dependencies = [
        ("asistencia", "0009_add_integrity_constraints_and_scanner_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="asistencia",
            name="puntualidad",
            field=models.CharField(
                choices=[
                    ("PUNTUAL", "Puntual"),
                    ("TARDE", "Tardanza"),
                    ("NO_APLICA", "No aplica"),
                ],
                default="NO_APLICA",
                help_text="Clasifica si el ingreso fue puntual o con tardanza.",
                max_length=12,
            ),
        ),
        migrations.RunPython(populate_puntualidad, reverse_code=migrations.RunPython.noop),
    ]
