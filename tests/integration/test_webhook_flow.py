import hashlib
import hmac
import json

from chaseless.db.models import (
    CandidateAction,
    Customer,
    Merchant,
    MerchantPolicyVersion,
    RecoveryAction,
    RecoveryCase,
    RecoveryRun,
    Subscription,
    WebhookEvent,
)
from chaseless.domain.enums import ActionStatus, CaseState
from chaseless.services.event_processor import process_webhook_event
from chaseless.services.webhooks import ingest_razorpay_webhook
from sqlalchemy.orm import Session

SECRET = "test-webhook-secret"


def payload(event: str, created_at: int, status: str, payment_status: str) -> bytes:
    return json.dumps(
        {
            "event": event,
            "created_at": created_at,
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_test_1",
                        "customer_id": "cust_test_1",
                        "plan_id": "plan_test",
                        "status": status,
                        "current_start": 1788200000,
                    }
                },
                "payment": {
                    "entity": {
                        "id": f"pay_{event.replace('.', '_')}_{created_at}",
                        "invoice_id": "inv_test_1",
                        "status": payment_status,
                        "amount": 125000,
                        "currency": "INR",
                        "error_code": "INSUFFICIENT_FUNDS" if payment_status == "failed" else None,
                    }
                },
            },
        },
        separators=(",", ":"),
    ).encode()


def ingest(db: Session, raw: bytes, event_id: str):
    signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return ingest_razorpay_webhook(
        db,
        raw_body=raw,
        signature=signature,
        provider_event_id=event_id,
        current_secret=SECRET,
    )


def test_pending_duplicate_and_charged_truth_loop(db: Session) -> None:
    db.add(Merchant(name="Test Merchant"))
    db.commit()
    pending = payload("subscription.pending", 1788200010, "pending", "failed")
    first = ingest(db, pending, "evt_pending_1")
    duplicate = ingest(db, pending, "evt_pending_1")
    assert not first.duplicate
    assert duplicate.duplicate
    assert first.event_id is not None
    process_webhook_event(db, first.event_id)
    db.commit()
    case = db.query(RecoveryCase).one()
    assert case.state == CaseState.DIAGNOSED.value

    charged = payload("subscription.charged", 1788200100, "active", "captured")
    success = ingest(db, charged, "evt_charged_1")
    assert success.event_id is not None
    process_webhook_event(db, success.event_id)
    db.commit()
    db.refresh(case)
    assert case.state == CaseState.RECOVERED_VERIFIED.value
    assert case.recovered_amount_minor == 125000


def test_delayed_pending_cannot_regress_subscription_or_create_case(db: Session) -> None:
    db.add(Merchant(name="Test Merchant"))
    db.commit()
    charged = payload("subscription.charged", 1788200200, "active", "captured")
    success = ingest(db, charged, "evt_charged_first")
    assert success.event_id is not None
    process_webhook_event(db, success.event_id)
    db.commit()

    delayed = payload("subscription.pending", 1788200100, "pending", "failed")
    old = ingest(db, delayed, "evt_pending_delayed")
    assert old.event_id is not None
    process_webhook_event(db, old.event_id)
    db.commit()
    subscription = db.query(Subscription).one()
    assert subscription.status == "active"
    assert db.query(RecoveryCase).count() == 0
    assert db.query(WebhookEvent).count() == 2


def test_payment_link_webhook_is_truth_for_recovery(db: Session) -> None:
    merchant = Merchant(name="Test Merchant")
    db.add(merchant)
    db.flush()
    customer = Customer(
        merchant_id=merchant.id,
        provider_customer_id="cust_link_test",
        display_name="Link Customer",
    )
    db.add(customer)
    db.flush()
    subscription = Subscription(
        merchant_id=merchant.id,
        customer_id=customer.id,
        razorpay_subscription_id="sub_link_test",
        status="pending",
        amount_minor=125000,
        currency="INR",
    )
    policy = MerchantPolicyVersion(
        merchant_id=merchant.id,
        version=1,
        rules_json={},
    )
    db.add_all([subscription, policy])
    db.flush()
    case = RecoveryCase(
        merchant_id=merchant.id,
        customer_id=customer.id,
        subscription_id=subscription.id,
        episode_key="sub_link_test:invoice:inv_link_test",
        state=CaseState.ACTION_EXECUTED.value,
        risk_amount_minor=125000,
        currency="INR",
    )
    run = RecoveryRun(
        merchant_id=merchant.id,
        policy_version_id=policy.id,
        status="EXECUTING",
        budget_minor=10000,
        contact_budget=10,
    )
    db.add_all([case, run])
    db.flush()
    candidate = CandidateAction(
        run_id=run.id,
        case_id=case.id,
        action_type="PAYMENT_LINK",
        probability_action=0.5,
        probability_natural=0.1,
        action_cost_minor=100,
        fatigue_penalty_minor=0,
        risk_penalty_minor=0,
        eirv_minor=49900,
        eligible=True,
        policy_verdict="ALLOW",
        selected=True,
    )
    db.add(candidate)
    db.flush()
    action = RecoveryAction(
        run_id=run.id,
        case_id=case.id,
        candidate_action_id=candidate.id,
        action_type="PAYMENT_LINK",
        status=ActionStatus.EXECUTING.value,
        idempotency_key="test-link-action",
        provider_reference="plink_test_1",
    )
    db.add(action)
    db.commit()

    raw = json.dumps(
        {
            "event": "payment_link.paid",
            "created_at": 1788200300,
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_test_1",
                        "amount_paid": 125000,
                        "currency": "INR",
                    }
                },
                "payment": {"entity": {"id": "pay_link_test_1", "amount": 125000}},
            },
        },
        separators=(",", ":"),
    ).encode()
    accepted = ingest(db, raw, "evt_link_paid_1")
    assert accepted.event_id is not None
    process_webhook_event(db, accepted.event_id)
    db.commit()
    db.refresh(case)
    db.refresh(action)

    assert case.state == CaseState.RECOVERED_VERIFIED.value
    assert case.recovered_amount_minor == 125000
    assert action.status == ActionStatus.SUCCEEDED.value
