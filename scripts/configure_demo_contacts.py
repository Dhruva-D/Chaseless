"""Configure the single safe demo recipient for all ChaseLess demo customers.

This only updates encrypted contact endpoints and explicit channel consent.  It
does not send a message or place a call.
"""

from __future__ import annotations

from chaseless.core.settings import get_settings
from chaseless.db.models import Customer, Merchant
from chaseless.db.session import session_scope
from chaseless.services.contacts import (
    store_email_endpoint,
    store_phone_endpoint,
    store_whatsapp_endpoint,
)

DEMO_MERCHANT_NAME = "ChaseLess Demo"
DEMO_RECIPIENT_E164 = "+916364768472"
DEMO_RECIPIENT_EMAIL = "dhruva.20052706@gmail.com"


def main() -> None:
    settings = get_settings()
    with session_scope() as db:
        merchant = db.query(Merchant).filter(Merchant.name == DEMO_MERCHANT_NAME).one_or_none()
        if merchant is None:
            raise SystemExit("ChaseLess Demo merchant not found; run seed_demo first.")

        customers = (
            db.query(Customer)
            .filter(Customer.merchant_id == merchant.id)
            .order_by(Customer.created_at)
            .all()
        )
        for customer in customers:
            # Persist ciphertext only; plaintext is never written to the database.
            store_whatsapp_endpoint(customer, settings, DEMO_RECIPIENT_E164, consent=True)
            store_phone_endpoint(
                customer, settings, channel="sms", e164=DEMO_RECIPIENT_E164, consent=True
            )
            store_phone_endpoint(
                customer, settings, channel="voice", e164=DEMO_RECIPIENT_E164, consent=True
            )
            store_email_endpoint(customer, settings, DEMO_RECIPIENT_EMAIL, consent=True)
            channel_order = {
                "cust_demo_0": ["email", "whatsapp", "sms"],
                "cust_demo_1": ["whatsapp", "sms", "email"],
                "cust_demo_2": ["email", "whatsapp", "sms"],
                "cust_demo_3": ["email", "whatsapp", "sms"],
            }.get(customer.provider_customer_id or "", ["whatsapp", "sms", "email"])
            customer.contact_preferences = {
                **customer.contact_preferences,
                "recovery_channel_order": channel_order,
            }

    print(f"Configured encrypted demo contacts for {len(customers)} customers.")


if __name__ == "__main__":
    main()
