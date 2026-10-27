from django.db import migrations, models


def deduplicate_attendance_and_justifications(apps, schema_editor):
    Asistencia = apps.get_model("asistencia", "Asistencia")
    Justificacion = apps.get_model("asistencia", "Justificacion")

    # Asistencia: mantener el registro mas reciente por (usuario, evento, fecha).
    keys = (
        Asistencia.objects.filter(evento__isnull=False)
        .values("usuario_id", "evento_id", "fecha")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    for key in keys:
        duplicates = Asistencia.objects.filter(
            usuario_id=key["usuario_id"],
            evento_id=key["evento_id"],
            fecha=key["fecha"],
        ).order_by("-id")
        keep = duplicates.first()
        duplicates.exclude(id=keep.id).delete()

    # Justificacion: mantener la justificacion mas reciente por (usuario, evento).
    jkeys = (
        Justificacion.objects.values("usuario_id", "evento_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
    )
    for key in jkeys:
        duplicates = Justificacion.objects.filter(
            usuario_id=key["usuario_id"],
            evento_id=key["evento_id"],
        ).order_by("-id")
        keep = duplicates.first()
        duplicates.exclude(id=keep.id).delete()


def grant_scan_permission_to_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group, _ = Group.objects.get_or_create(name="Escaneadores")
    perm = Permission.objects.filter(
        codename="can_scan_qr",
        content_type__app_label="asistencia",
        content_type__model="usuario",
    ).first()
    if perm:
        group.permissions.add(perm)


def revoke_scan_permission_from_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group = Group.objects.filter(name="Escaneadores").first()
    if not group:
        return
    perm = Permission.objects.filter(
        codename="can_scan_qr",
        content_type__app_label="asistencia",
        content_type__model="usuario",
    ).first()
    if perm:
        group.permissions.remove(perm)


class Migration(migrations.Migration):
    dependencies = [
        ("asistencia", "0008_alter_asistencia_fecha_alter_justificacion_evidencia"),
    ]

    operations = [
        migrations.RunPython(
            deduplicate_attendance_and_justifications,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="asistencia",
            constraint=models.UniqueConstraint(
                condition=models.Q(evento__isnull=False),
                fields=("usuario", "evento", "fecha"),
                name="uniq_asistencia_usuario_evento_fecha",
            ),
        ),
        migrations.AddConstraint(
            model_name="justificacion",
            constraint=models.UniqueConstraint(
                fields=("usuario", "evento"),
                name="uniq_justificacion_usuario_evento",
            ),
        ),
        migrations.RunPython(
            grant_scan_permission_to_group,
            reverse_code=revoke_scan_permission_from_group,
        ),
    ]
