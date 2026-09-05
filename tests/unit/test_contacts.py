from chaseless.core.contact_crypto import ContactEncryptionError
from chaseless.core.settings import Settings
from chaseless.db.models import Customer, Merchant
from chaseless.services.contacts import store_whatsapp_endpoint, whatsapp_endpoint
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session


def test_store_whatsapp_endpoint_encrypts_and_requires_consent(db: Session) -> None:
    merchant = Merchant(name="Test Merchant")
    db.add(merchant)
    db.flush()
    customer = Customer(merchant_id=merchant.id, display_name="Test Customer")
    db.add(customer)
    db.flush()
    settings = Settings(field_encryption_key=Fernet.generate_key().decode("utf-8"))

    masked = store_whatsapp_endpoint(customer, settings, "+919876543210", consent=True)

    assert masked == "+91******3210"
    assert customer.whatsapp_e164_encrypted is not None
    assert "+919876543210" not in customer.whatsapp_e164_encrypted
    assert whatsapp_endpoint(customer, settings) == "+919876543210"

    store_whatsapp_endpoint(customer, settings, "+919876543210", consent=False)
    try:
        whatsapp_endpoint(customer, settings)
    except ContactEncryptionError as exc:
        assert "consent" in str(exc).lower()
    else:
        raise AssertionError("Expected WhatsApp delivery to require consent")
