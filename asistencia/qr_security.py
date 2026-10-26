import base64
import logging
from uuid import UUID

from django.conf import settings
from django.core.signing import BadSignature, Signer

logger = logging.getLogger(__name__)

QR_SIGNING_SALT = "asistencia.qr.v1"
QR_PAYLOAD_PREFIX = "v1"


def _get_config():
    try:
        from .models import ConfiguracionSistema
        return ConfiguracionSistema.objects.first()
    except Exception:
        return None


def _get_primary_qr_key():
    config = _get_config()
    if config and config.qr_signing_key and config.qr_signing_key.strip():
        return config.qr_signing_key.strip()
    return getattr(settings, "QR_SIGNING_KEY", None) or settings.SECRET_KEY


def _get_fallback_qr_keys():
    keys = list(getattr(settings, "QR_SIGNING_FALLBACK_KEYS", []))
    config = _get_config()
    if config and config.qr_fallback_keys:
        for k in config.qr_fallback_keys.split(","):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
    return keys


def is_rescue_mode_enabled():
    config = _get_config()
    if config is not None:
        return bool(config.permitir_carnets_antiguos_sin_firma)
    return getattr(settings, "QR_ALLOW_UNSIGNED_FALLBACK", True)


def _build_signer(key):
    return Signer(key=key, salt=QR_SIGNING_SALT)


def build_qr_encoded_payload(qr_uid, qr_version):
    raw_payload = f"{QR_PAYLOAD_PREFIX}|{qr_uid}|{qr_version}"
    signed_payload = _build_signer(_get_primary_qr_key()).sign(raw_payload)
    return base64.b64encode(signed_payload.encode()).decode()


def decode_qr_encoded_payload(encoded_qr_data):
    if isinstance(encoded_qr_data, str):
        encoded_qr_data = encoded_qr_data.strip()

    # Normalizar padding si fuera necesario
    missing_padding = len(encoded_qr_data) % 4
    if missing_padding:
        encoded_qr_data += '=' * (4 - missing_padding)

    try:
        signed_payload = base64.b64decode(encoded_qr_data.encode('ascii', errors='ignore')).decode('utf-8', errors='replace')
    except Exception as exc:
        raise BadSignature("Invalid base64 payload") from exc

    unsigned_payload = None
    errors = []
    # 1. Intentar validar la firma criptográfica con la clave actual y claves de respaldo
    for key in [_get_primary_qr_key(), *_get_fallback_qr_keys()]:
        if not key:
            continue
        try:
            unsigned_payload = _build_signer(key).unsign(signed_payload)
            break
        except BadSignature as exc:
            errors.append(exc)

    # 2. Si la firma no coincide (por ejemplo, clave perdida en migración de servidor),
    # verificamos si el modo de rescate/compatibilidad está activo.
    if unsigned_payload is None:
        if is_rescue_mode_enabled():
            logger.info("QR verificado mediante modo rescate/compatibilidad (firma no validada).")
            # Django Signer concatena datos con ':' -> <payload>:<firma>
            if ":" in signed_payload:
                unsigned_payload = signed_payload.rsplit(":", 1)[0]
            else:
                unsigned_payload = signed_payload
        else:
            raise errors[-1] if errors else BadSignature("Invalid QR signature")

    if unsigned_payload.startswith(f"{QR_PAYLOAD_PREFIX}|"):
        parts = unsigned_payload.split("|")
        if len(parts) != 3:
            raise BadSignature("Invalid structured QR payload")

        _, qr_uid_str, qr_version_str = parts
        try:
            qr_uid = UUID(qr_uid_str)
            qr_version = int(qr_version_str)
        except (ValueError, TypeError) as exc:
            raise BadSignature("Invalid UUID or version in QR payload") from exc
        return {
            "kind": "uid_v1",
            "qr_uid": qr_uid,
            "qr_version": qr_version,
        }

    return {
        "kind": "legacy_dni",
        "dni": unsigned_payload,
    }

