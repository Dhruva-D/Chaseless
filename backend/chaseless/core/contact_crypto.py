from __future__ import annotations

import re

from cryptography.fernet import Fernet, InvalidToken

from chaseless.core.settings import Settings

E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ContactEncryptionError(ValueError):
    pass


class ContactCipher:
    """Small boundary for contact PII: storage is encrypted, logs retain only masks."""

    def __init__(self, settings: Settings) -> None:
        if not settings.field_encryption_key:
            raise ContactEncryptionError("FIELD_ENCRYPTION_KEY is required for contact delivery")
        try:
            self._fernet = Fernet(settings.field_encryption_key.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ContactEncryptionError("FIELD_ENCRYPTION_KEY is not a valid Fernet key") from exc

    def encrypt_e164(self, value: str) -> str:
        normalized = value.strip().replace(" ", "")
        if not E164_PATTERN.fullmatch(normalized):
            raise ContactEncryptionError("Contact endpoint must be E.164 formatted")
        return self._fernet.encrypt(normalized.encode("utf-8")).decode("utf-8")

    def decrypt_e164(self, value: str) -> str:
        try:
            decoded = self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ContactEncryptionError("Encrypted contact endpoint cannot be decrypted") from exc
        if not E164_PATTERN.fullmatch(decoded):
            raise ContactEncryptionError("Decrypted contact endpoint is invalid")
        return decoded

    def encrypt_email(self, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ContactEncryptionError("Email endpoint is invalid")
        return self._fernet.encrypt(normalized.encode("utf-8")).decode("utf-8")

    def decrypt_email(self, value: str) -> str:
        try:
            decoded = self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ContactEncryptionError("Encrypted email endpoint cannot be decrypted") from exc
        if not EMAIL_PATTERN.fullmatch(decoded):
            raise ContactEncryptionError("Decrypted email endpoint is invalid")
        return decoded


def mask_e164(value: str) -> str:
    normalized = value.strip().replace(" ", "")
    if not E164_PATTERN.fullmatch(normalized):
        raise ContactEncryptionError("Contact endpoint must be E.164 formatted")
    return f"{normalized[:3]}******{normalized[-4:]}"


def mask_email(value: str) -> str:
    normalized = value.strip().lower()
    if not EMAIL_PATTERN.fullmatch(normalized):
        raise ContactEncryptionError("Email endpoint is invalid")
    local, domain = normalized.split("@", 1)
    return f"{local[:2]}***@{domain}"
