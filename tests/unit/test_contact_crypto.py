from chaseless.core.contact_crypto import ContactCipher, ContactEncryptionError, mask_e164
from chaseless.core.settings import Settings
from cryptography.fernet import Fernet


def test_contact_cipher_round_trip_and_mask() -> None:
    cipher = ContactCipher(Settings(field_encryption_key=Fernet.generate_key().decode("utf-8")))
    encrypted = cipher.encrypt_e164("+919876543210")

    assert "+919876543210" not in encrypted
    assert cipher.decrypt_e164(encrypted) == "+919876543210"
    assert mask_e164("+919876543210") == "+91******3210"


def test_contact_cipher_rejects_non_e164_values() -> None:
    cipher = ContactCipher(Settings(field_encryption_key=Fernet.generate_key().decode("utf-8")))

    try:
        cipher.encrypt_e164("9876543210")
    except ContactEncryptionError as exc:
        assert "E.164" in str(exc)
    else:
        raise AssertionError("Expected invalid contact endpoint to be rejected")
