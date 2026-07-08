from __future__ import annotations

from dataclasses import dataclass

from ..models import Asistencia, Usuario


@dataclass(frozen=True)
class AttendanceFlags:
    is_exonerado: bool
    is_justificada: bool
    is_late_absent: bool
    is_absent: bool
    is_pending: bool
    is_confirmed_presence: bool


def classify_attendance_item(item, tardanza_activa: bool) -> AttendanceFlags:
    """
    Regla única para clasificar un registro de asistencia/reporte.
    EXONERADO no se penaliza por tardanza.
    """
    estado_usuario = getattr(getattr(item, "usuario", None), "estado", None)
    is_exonerado = estado_usuario == Usuario.ESTADO_EXONERADO
    is_justificada = bool(getattr(item, "es_justificada", False))
    is_late_absent = bool(
        tardanza_activa
        and getattr(item, "puntualidad", None) == Asistencia.PUNTUALIDAD_TARDE
        and not is_exonerado
    )
    is_absent = bool(is_justificada or is_late_absent or getattr(item, "is_absent", False))
    has_salida = bool(getattr(item, "hora_salida", None))
    is_pending = bool(not is_absent and not has_salida and not is_exonerado)
    is_confirmed_presence = bool(not is_absent and (has_salida or is_exonerado))

    return AttendanceFlags(
        is_exonerado=is_exonerado,
        is_justificada=is_justificada,
        is_late_absent=is_late_absent,
        is_absent=is_absent,
        is_pending=is_pending,
        is_confirmed_presence=is_confirmed_presence,
    )

