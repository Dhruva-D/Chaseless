"""Preview and import subscription-linked Razorpay Test Mode payment failures.

The import intentionally does not execute recovery actions. It creates a diagnosed
case with an audit trail, leaving all outbound contact subject to the normal policy
and approval workflow.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from chaseless.core.settings import Settings
from chaseless.db.models import Customer, PaymentEvent, RecoveryCase, Subscription, WebhookEvent
from chaseless.domain.diagnosis import diagnose
from chaseless.domain.enums import CaseState
from chaseless.domain.types import RecoveryContext
from chaseless.integrations.razorpay.client import RazorpayClient
from chaseless.services.audit import append_audit, canonical_hash
from chaseless.services.webhooks import active_merchant


@dataclass(frozen=True)
class TestPaymentCandidate:
    payment_id: str
    amount_minor: int
    currency: str
    occurred_at: datetime
    failure_code: str | None
    failure_reason: str | None
    failure_description: str | None
    subscription_id: str | None
    invoice_id: str | None
    eligible: bool
    skip_reason: str | None
    subscription: dict[str, Any] | None


def _utc_timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    return datetime.now(UTC)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _subscription_id(
    payment: dict[str, Any], client: RazorpayClient
) -> tuple[str | None, str | None]:
    direct = _string(payment.get("subscription_id"))
    invoice_id = _string(payment.get("invoice_id"))
    if direct:
        return direct, invoice_id
    notes = payment.get("notes")
    if isinstance(notes, dict):
        noted = _string(notes.get("subscription_id"))
        if noted:
            return noted, invoice_id
    if not invoice_id:
        return None, None
    invoice = client.fetch_invoice(invoice_id)
    return (_string(invoice.get("subscription_id")) if invoice else None), invoice_id


def preview_failed_subscription_payments(
    settings: Settings, *, count: int
) -> list[TestPaymentCandidate]:
    client = RazorpayClient(settings)
    candidates: list[TestPaymentCandidate] = []
    for payment in client.fetch_recent_payments(count=count):
        if payment.get("status") != "failed":
            continue
        payment_id = _string(payment.get("id"))
        if not payment_id:
            continue
        subscription_id, invoice_id = _subscription_id(payment, client)
        subscription = client.fetch_subscription(subscription_id) if subscription_id else None
        eligible = subscription_id is not None and subscription is not None
        candidates.append(
            TestPaymentCandidate(
                payment_id=payment_id,
                amount_minor=max(int(payment.get("amount") or 0), 0),
                currency=str(payment.get("currency") or "INR"),
                occurred_at=_utc_timestamp(payment.get("created_at")),
                failure_code=_string(payment.get("error_code"))
                or _string(payment.get("error_reason")),
                failure_reason=_string(payment.get("error_reason")),
                failure_description=_string(payment.get("error_description")),
                subscription_id=subscription_id,
                invoice_id=invoice_id,
                eligible=eligible,
                skip_reason=None
                if eligible
                else "No subscription-linked Razorpay record was found",
                subscription=subscription,
            )
        )
    return candidates


def _recommendation(failure_code: str | None) -> tuple[str, str]:
    normalized = (failure_code or "").upper()
    if normalized in {"CARD_EXPIRED", "TOKEN_EXPIRED", "MANDATE_INVALID"}:
        return "UPDATE_PAYMENT_METHOD", "Request a secure payment-method update"
    if normalized in {"INSUFFICIENT_FUNDS", "LOW_BALANCE", "BANK_ACCOUNT_INSUFFICIENT"}:
        return "NATIVE_RETRY_WAIT", "Wait for the native retry before contacting"
    return "NUDGE", "Prepare one policy-bounded reminder; do not send automatically"


def _test_customer_name(provider_customer_id: str) -> str:
    return f"Test customer · {provider_customer_id[-6:]}"


def import_selected_test_payments(
    db: Session, settings: Settings, *, payment_ids: list[str]
) -> tuple[list[uuid.UUID], list[str]]:
    selected = set(payment_ids)
    candidates = [
        candidate
        for candidate in preview_failed_subscription_payments(settings, count=100)
        if candidate.payment_id in selected
    ]
    merchant = active_merchant(db)
    imported: list[uuid.UUID] = []
    skipped: list[str] = []

    for candidate in candidates:
        if not candidate.eligible or not candidate.subscription or not candidate.subscription_id:
            skipped.append(candidate.payment_id)
            continue
        episode_key = f"razorpay-test-import:{candidate.payment_id}"
        existing_case = (
            db.query(RecoveryCase)
            .filter(
                RecoveryCase.merchant_id == merchant.id, RecoveryCase.episode_key == episode_key
            )
            .one_or_none()
        )
        if existing_case:
            imported.append(existing_case.id)
            continue

        provider_customer_id = _string(candidate.subscription.get("customer_id")) or (
            f"test-payment:{candidate.payment_id}"
        )
        customer = (
            db.query(Customer)
            .filter(
                Customer.merchant_id == merchant.id,
                Customer.provider_customer_id == provider_customer_id,
            )
            .one_or_none()
        )
        if customer is None:
            customer = Customer(
                merchant_id=merchant.id,
                provider_customer_id=provider_customer_id,
                display_name=_test_customer_name(provider_customer_id),
                contact_preferences={"segment": "Razorpay Test subscription"},
                consent={},
            )
            db.add(customer)
            db.flush()

        subscription = (
            db.query(Subscription)
            .filter(
                Subscription.merchant_id == merchant.id,
                Subscription.razorpay_subscription_id == candidate.subscription_id,
            )
            .one_or_none()
        )
        if subscription is None:
            subscription = Subscription(
                merchant_id=merchant.id,
                customer_id=customer.id,
                razorpay_subscription_id=candidate.subscription_id,
                razorpay_plan_id=_string(candidate.subscription.get("plan_id")),
                status=str(candidate.subscription.get("status") or "pending"),
                amount_minor=max(candidate.amount_minor, 1),
                currency=candidate.currency,
                next_charge_at=_utc_timestamp(candidate.subscription.get("current_end")),
                provider_updated_at=candidate.occurred_at,
            )
            db.add(subscription)
            db.flush()

        diagnosis = diagnose(
            RecoveryContext(
                case_id="pending-import",
                amount_minor=max(candidate.amount_minor, 1),
                currency=candidate.currency,
                subscription_status=subscription.status,
                failure_code=candidate.failure_code,
                consent_channels=set(),
            )
        )
        action, next_step = _recommendation(candidate.failure_code)
        customer.contact_preferences = {
            **customer.contact_preferences,
            "source_type": "Failed subscription · Razorpay Test API",
            "recommended_action": action,
            "next_step": next_step,
            "priority": "HIGH" if candidate.amount_minor >= 100_000 else "MEDIUM",
        }
        case = RecoveryCase(
            merchant_id=merchant.id,
            customer_id=customer.id,
            subscription_id=subscription.id,
            episode_key=episode_key,
            state=CaseState.DIAGNOSED.value,
            risk_amount_minor=max(candidate.amount_minor, 1),
            currency=candidate.currency,
            diagnosis=diagnosis.model_dump(mode="json"),
            natural_recovery_score=diagnosis.natural_recovery_score,
        )
        db.add(case)
        db.flush()

        sanitized_payment = {
            "id": candidate.payment_id,
            "amount": candidate.amount_minor,
            "currency": candidate.currency,
            "status": "failed",
            "subscription_id": candidate.subscription_id,
            "invoice_id": candidate.invoice_id,
            "error_code": candidate.failure_code,
            "error_reason": candidate.failure_reason,
            "error_description": candidate.failure_description,
        }
        source_event = WebhookEvent(
            merchant_id=merchant.id,
            provider_event_id=f"razorpay-test-api:{candidate.payment_id}",
            event_type="payment.failed.api_import",
            signature_valid=False,
            raw_hash=canonical_hash(sanitized_payment),
            payload={"_chaseless_source": "razorpay_test_api_import", "payment": sanitized_payment},
            occurred_at=candidate.occurred_at,
            processed_at=datetime.now(UTC),
        )
        db.add(source_event)
        db.flush()
        db.add(
            PaymentEvent(
                merchant_id=merchant.id,
                webhook_event_id=source_event.id,
                subscription_id=subscription.id,
                provider_payment_id=candidate.payment_id,
                provider_invoice_id=candidate.invoice_id,
                event_kind="payment.failed.api_import",
                status="failed",
                amount_minor=candidate.amount_minor,
                currency=candidate.currency,
                failure_code=candidate.failure_code,
                failure_description=candidate.failure_description,
                occurred_at=candidate.occurred_at,
            )
        )
        append_audit(
            db,
            merchant_id=merchant.id,
            aggregate_type="recovery_case",
            aggregate_id=case.id,
            event_kind="RAZORPAY_TEST_PAYMENT_IMPORTED",
            actor_type="integration",
            actor_id="razorpay_test_api",
            decision={
                "input": sanitized_payment,
                "output": {"case_state": case.state, "recommended_action": action},
            },
        )
        imported.append(case.id)
    db.commit()
    return imported, skipped
