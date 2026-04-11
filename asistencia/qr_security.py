import base64
from uuid import UUID

from django.conf import settings
from django.core.signing import BadSignature, Signer


QR_SIGNING_SALT = "asistencia.qr.v1"
QR_PAYLOAD_PREFIX = "v1"


def _get_primary_qr_key():
    return getattr(settings, "QR_SIGNING_KEY", None) or settings.SECRET_KEY


def _get_fallback_qr_keys():
    return getattr(settings, "QR_SIGNING_FALLBACK_KEYS", [])


def _build_signer(key):
    return Signer(key=key, salt=QR_SIGNING_SALT)


def build_qr_encoded_payload(qr_uid, qr_version):
    raw_payload = f"{QR_PAYLOAD_PREFIX}|{qr_uid}|{qr_version}"
    signed_payload = _build_signer(_get_primary_qr_key()).sign(raw_payload)
    return base64.b64encode(signed_payload.encode()).decode()


def decode_qr_encoded_payload(encoded_qr_data):
    signed_payload = base64.b64decode(encoded_qr_data).decode()

    errors = []
    for key in [_get_primary_qr_key(), *_get_fallback_qr_keys()]:
        try:
            unsigned_payload = _build_signer(key).unsign(signed_payload)
            break
        except BadSignature as exc:
            errors.append(exc)
    else:
        raise errors[-1] if errors else BadSignature("Invalid QR signature")

    if unsigned_payload.startswith(f"{QR_PAYLOAD_PREFIX}|"):
        parts = unsigned_payload.split("|")
        if len(parts) != 3:
            raise BadSignature("Invalid structured QR payload")

        _, qr_uid_str, qr_version_str = parts
        qr_uid = UUID(qr_uid_str)
        qr_version = int(qr_version_str)
        return {
            "kind": "uid_v1",
            "qr_uid": qr_uid,
            "qr_version": qr_version,
        }

    return {
        "kind": "legacy_dni",
        "dni": unsigned_payload,
    }
