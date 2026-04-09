import json

from .models import LogAccion


def log_critical_change(actor, entity, before, after, action_label="Cambio critico"):
    """
    Registra una auditoria con before/after en LogAccion.
    """
    before = before or {}
    after = after or {}
    changed = {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted(set(before.keys()) | set(after.keys()))
        if before.get(key) != after.get(key)
    }

    payload = {
        "entity": entity,
        "action": action_label,
        "before": before,
        "after": after,
        "changed": changed,
    }
    LogAccion.objects.create(
        usuario=actor,
        accion=f"Auditoria: {action_label}",
        descripcion=json.dumps(payload, ensure_ascii=False),
    )
