from __future__ import annotations

from chaseless.core.contact_crypto import (
    ContactCipher,
    ContactEncryptionError,
    mask_e164,
    mask_email,
)
from chaseless.core.settings import Settings
from chaseless.db.models import Customer


def store_whatsapp_endpoint(
    customer: Customer, settings: Settings, e164: str, consent: bool
) -> str:
    """Persist a masked, consented WhatsApp endpoint without retaining plaintext."""
    cipher = ContactCipher(settings)
    customer.whatsapp_e164_encrypted = cipher.encrypt_e164(e164)
    customer.phone_masked = mask_e164(e164)
    customer.consent = {**customer.consent, "whatsapp": consent}
    return customer.phone_masked


def whatsapp_endpoint(customer: Customer, settings: Settings) -> str:
    """Return a contact only when messaging consent and an encrypted endpoint both exist."""
    if not customer.consent.get("whatsapp"):
        raise ContactEncryptionError("WhatsApp consent is required")
    if not customer.whatsapp_e164_encrypted:
        raise ContactEncryptionError("No encrypted WhatsApp endpoint is available")
    return ContactCipher(settings).decrypt_e164(customer.whatsapp_e164_encrypted)


def store_phone_endpoint(
    customer: Customer, settings: Settings, *, channel: str, e164: str, consent: bool
) -> str:
    """Store one encrypted phone endpoint for SMS or voice with per-channel consent."""
    if channel not in {"sms", "voice"}:
        raise ContactEncryptionError("Unsupported phone channel")
    cipher = ContactCipher(settings)
    customer.phone_e164_encrypted = cipher.encrypt_e164(e164)
    customer.phone_masked = mask_e164(e164)
    customer.consent = {**customer.consent, channel: consent}
    return customer.phone_masked


def phone_endpoint(customer: Customer, settings: Settings, *, channel: str) -> str:
    if not customer.consent.get(channel):
        raise ContactEncryptionError(f"{channel.title()} consent is required")
    if not customer.phone_e164_encrypted:
        raise ContactEncryptionError("No encrypted phone endpoint is available")
    return ContactCipher(settings).decrypt_e164(customer.phone_e164_encrypted)


def store_email_endpoint(customer: Customer, settings: Settings, email: str, consent: bool) -> str:
    cipher = ContactCipher(settings)
    customer.email_encrypted = cipher.encrypt_email(email)
    customer.email_masked = mask_email(email)
    customer.consent = {**customer.consent, "email": consent}
    return customer.email_masked


def email_endpoint(customer: Customer, settings: Settings) -> str:
    if not customer.consent.get("email"):
        raise ContactEncryptionError("Email consent is required")
    if not customer.email_encrypted:
        raise ContactEncryptionError("No encrypted email endpoint is available")
    return ContactCipher(settings).decrypt_email(customer.email_encrypted)
