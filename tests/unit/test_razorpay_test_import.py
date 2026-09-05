from chaseless.core.settings import Settings
from chaseless.db.models import Merchant, PaymentEvent, RecoveryCase, WebhookEvent
from chaseless.services import razorpay_test_import
from sqlalchemy.orm import Session


class FakeRazorpayClient:
    def __init__(self, _: Settings) -> None:
        pass

    def fetch_recent_payments(self, *, count: int) -> list[dict[str, object]]:
        assert count in {25, 100}
        return [
            {
                "id": "pay_test_failed_1",
                "status": "failed",
                "amount": 129900,
                "currency": "INR",
                "created_at": 1_700_000_000,
                "subscription_id": "sub_test_123",
                "error_reason": "CARD_EXPIRED",
                "error_description": "The customer's test card has expired",
                "email": "should-not-be-stored@example.com",
                "contact": "+919999999999",
            },
            {"id": "pay_unlinked", "status": "failed", "amount": 1000, "currency": "INR"},
        ]

    def fetch_invoice(self, _: str) -> dict[str, object] | None:
        return None

    def fetch_subscription(self, subscription_id: str) -> dict[str, object] | None:
        if subscription_id != "sub_test_123":
            return None
        return {
            "id": subscription_id,
            "customer_id": "cust_test_456",
            "status": "active",
            "plan_id": "plan_test",
        }


def test_test_mode_import_only_creates_subscription_linked_pii_free_case(
    db: Session, monkeypatch
) -> None:
    db.add(Merchant(name="Importer Merchant"))
    db.commit()
    monkeypatch.setattr(razorpay_test_import, "RazorpayClient", FakeRazorpayClient)
    settings = Settings(
        app_env="test", razorpay_key_id="rzp_test_fake", razorpay_key_secret="secret"
    )

    preview = razorpay_test_import.preview_failed_subscription_payments(settings, count=25)
    assert [item.eligible for item in preview] == [True, False]

    imported, skipped = razorpay_test_import.import_selected_test_payments(
        db, settings, payment_ids=["pay_test_failed_1", "pay_unlinked"]
    )

    assert len(imported) == 1
    assert skipped == ["pay_unlinked"]
    case = db.query(RecoveryCase).one()
    assert case.episode_key == "razorpay-test-import:pay_test_failed_1"
    assert case.diagnosis["failure_class"] == "INSTRUMENT_ISSUE"
    event = db.query(WebhookEvent).one()
    assert event.signature_valid is False
    assert event.payload["_chaseless_source"] == "razorpay_test_api_import"
    assert "email" not in event.payload["payment"]
    assert db.query(PaymentEvent).one().provider_payment_id == "pay_test_failed_1"

    repeated, _ = razorpay_test_import.import_selected_test_payments(
        db, settings, payment_ids=["pay_test_failed_1"]
    )
    assert repeated == imported
    assert db.query(RecoveryCase).count() == 1
