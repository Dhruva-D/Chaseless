from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from chaseless.core.settings import Settings
from chaseless.db.models import (
    Customer,
    PaymentEvent,
    RecoveryAction,
    RecoveryCase,
    Subscription,
    WebhookEvent,
)
from chaseless.domain.diagnosis import diagnose
from chaseless.domain.enums import ActionStatus, CaseState
from chaseless.domain.types import RecoveryContext
from chaseless.integrations.llm import diagnose_with_advisory
from chaseless.services.audit import append_audit

SUPPORTED_EVENTS = {
    "subscription.pending",
    "subscription.halted",
    "subscription.charged",
    "subscription.activated",
    "subscription.cancelled",
    "subscription.completed",
    "payment_link.paid",
    "payment_link.partially_paid",
    "payment_link.cancelled",
    "payment_link.expired",
}


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return payload.get("payload", {}).get(name, {}).get("entity", {}) or {}


def _event_time(event: WebhookEvent) -> datetime:
    return event.occurred_at or event.received_at


def _upsert_subscription_context(
    db: Session, event: WebhookEvent
) -> tuple[Customer, Subscription, dict[str, Any], dict[str, Any]]:
    subscription_data = _entity(event.payload, "subscription")
    payment_data = _entity(event.payload, "payment")
    provider_subscription_id = subscription_data.get("id")
    if not provider_subscription_id:
        raise ValueError("Subscription webhook does not contain a subscription id")
    provider_customer_id = (
        subscription_data.get("customer_id") or f"unknown:{provider_subscription_id}"
    )
    customer = (
        db.query(Customer)
        .filter(
            Customer.merchant_id == event.merchant_id,
            Customer.provider_customer_id == provider_customer_id,
        )
        .one_or_none()
    )
    if customer is None:
        customer = Customer(
            merchant_id=event.merchant_id,
            provider_customer_id=provider_customer_id,
            display_name=f"Customer {provider_customer_id[-6:]}",
        )
        db.add(customer)
        db.flush()

    subscription = (
        db.query(Subscription)
        .filter(
            Subscription.merchant_id == event.merchant_id,
            Subscription.razorpay_subscription_id == provider_subscription_id,
        )
        .one_or_none()
    )
    event_time = _event_time(event)
    amount = int(payment_data.get("amount") or 0)
    if subscription is None:
        subscription = Subscription(
            merchant_id=event.merchant_id,
            customer_id=customer.id,
            razorpay_subscription_id=provider_subscription_id,
            razorpay_plan_id=subscription_data.get("plan_id"),
            status=subscription_data.get("status") or "unknown",
            amount_minor=amount,
            currency=payment_data.get("currency") or "INR",
            provider_updated_at=event_time,
        )
        db.add(subscription)
        db.flush()
    elif subscription.provider_updated_at is None or event_time >= subscription.provider_updated_at:
        subscription.status = subscription_data.get("status") or subscription.status
        subscription.provider_updated_at = event_time
        if amount:
            subscription.amount_minor = amount
            subscription.currency = payment_data.get("currency") or subscription.currency
    return customer, subscription, subscription_data, payment_data


def _episode_key(subscription_data: dict[str, Any], payment_data: dict[str, Any]) -> str:
    subscription_id = str(subscription_data["id"])
    invoice_id = payment_data.get("invoice_id")
    if invoice_id:
        return f"{subscription_id}:invoice:{invoice_id}"
    cycle = (
        subscription_data.get("current_start") or subscription_data.get("charge_at") or "current"
    )
    return f"{subscription_id}:cycle:{cycle}"


def _record_payment_event(
    db: Session,
    *,
    webhook: WebhookEvent,
    subscription: Subscription,
    payment_data: dict[str, Any],
) -> None:
    provider_payment_id = payment_data.get("id") or f"event:{webhook.provider_event_id}"
    existing = (
        db.query(PaymentEvent)
        .filter(
            PaymentEvent.merchant_id == webhook.merchant_id,
            PaymentEvent.provider_payment_id == provider_payment_id,
            PaymentEvent.event_kind == webhook.event_type,
        )
        .one_or_none()
    )
    if existing:
        return
    error = payment_data.get("error_description") or payment_data.get("error_reason")
    db.add(
        PaymentEvent(
            merchant_id=webhook.merchant_id,
            webhook_event_id=webhook.id,
            subscription_id=subscription.id,
            provider_payment_id=provider_payment_id,
            provider_invoice_id=payment_data.get("invoice_id"),
            event_kind=webhook.event_type,
            status=payment_data.get("status") or subscription.status,
            amount_minor=int(payment_data.get("amount") or subscription.amount_minor),
            currency=payment_data.get("currency") or subscription.currency,
            failure_code=payment_data.get("error_code") or payment_data.get("error_reason"),
            failure_description=error,
            occurred_at=_event_time(webhook),
        )
    )


def _context_for_case(
    case: RecoveryCase, customer: Customer, subscription: Subscription, payment: dict[str, Any]
) -> RecoveryContext:
    return RecoveryContext(
        case_id=str(case.id),
        amount_minor=max(case.risk_amount_minor, 1),
        currency=case.currency,
        subscription_status=subscription.status,
        failure_code=payment.get("error_code") or payment.get("error_reason"),
        contacts_24h=customer.contacts_24h,
        contacts_7d=customer.contacts_7d,
        opted_out=customer.opted_out,
        promise_to_pay_at=customer.promise_to_pay_at,
        customer_timezone="Asia/Kolkata",
        intervention_count=case.contact_count,
        consent_channels={key for key, value in customer.consent.items() if value},
    )


def _handle_at_risk(
    db: Session,
    event: WebhookEvent,
    customer: Customer,
    subscription: Subscription,
    subscription_data: dict[str, Any],
    payment_data: dict[str, Any],
    settings: Settings | None = None,
) -> RecoveryCase:
    key = _episode_key(subscription_data, payment_data)
    case = (
        db.query(RecoveryCase)
        .filter(RecoveryCase.merchant_id == event.merchant_id, RecoveryCase.episode_key == key)
        .one_or_none()
    )
    if case is None:
        case = RecoveryCase(
            merchant_id=event.merchant_id,
            customer_id=customer.id,
            subscription_id=subscription.id,
            episode_key=key,
            state=CaseState.AT_RISK.value,
            risk_amount_minor=max(int(payment_data.get("amount") or subscription.amount_minor), 1),
            currency=payment_data.get("currency") or subscription.currency,
        )
        db.add(case)
        db.flush()
    if case.state == CaseState.RECOVERED_VERIFIED.value:
        return case

    context = _context_for_case(case, customer, subscription, payment_data)
    baseline = diagnose(context)
    result = (
        diagnose_with_advisory(context, baseline, settings) if settings is not None else baseline
    )
    case.diagnosis = result.model_dump(mode="json")
    case.natural_recovery_score = result.natural_recovery_score
    case.state = CaseState.DIAGNOSED.value
    case.state_version += 1
    append_audit(
        db,
        merchant_id=event.merchant_id,
        aggregate_type="recovery_case",
        aggregate_id=case.id,
        event_kind="CASE_DIAGNOSED",
        actor_type="system",
        actor_id=result.source,
        decision={
            "input": context.model_dump(mode="json"),
            "output": result.model_dump(mode="json"),
        },
    )
    return case


def _handle_charged(
    db: Session,
    event: WebhookEvent,
    subscription: Subscription,
    payment_data: dict[str, Any],
) -> None:
    cases = (
        db.query(RecoveryCase)
        .filter(
            RecoveryCase.subscription_id == subscription.id,
            RecoveryCase.state.notin_(
                [CaseState.RECOVERED_VERIFIED.value, CaseState.STOPPED.value]
            ),
        )
        .all()
    )
    amount = int(payment_data.get("amount") or subscription.amount_minor)
    for case in cases:
        case.state = CaseState.RECOVERED_VERIFIED.value
        case.state_version += 1
        case.recovered_amount_minor = min(amount, case.risk_amount_minor)
        case.recovered_at = _event_time(event)
        append_audit(
            db,
            merchant_id=event.merchant_id,
            aggregate_type="recovery_case",
            aggregate_id=case.id,
            event_kind="RECOVERY_VERIFIED",
            actor_type="webhook",
            actor_id=event.provider_event_id,
            decision={
                "payment_id": payment_data.get("id"),
                "amount_minor": case.recovered_amount_minor,
                "source_event": event.event_type,
            },
        )


def _handle_payment_link_event(db: Session, event: WebhookEvent) -> None:
    link_data = _entity(event.payload, "payment_link")
    payment_data = _entity(event.payload, "payment")
    link_id = link_data.get("id")
    if not link_id:
        raise ValueError("Payment Link event does not contain a link id")
    action = (
        db.query(RecoveryAction).filter(RecoveryAction.provider_reference == link_id).one_or_none()
    )
    if action is None:
        return
    case = db.get(RecoveryCase, action.case_id)
    if case is None:
        raise ValueError("Payment Link action has no recovery case")
    action.result = {**action.result, "webhook_event": event.event_type, "link": link_data}
    if event.event_type == "payment_link.paid":
        paid_amount = int(link_data.get("amount_paid") or payment_data.get("amount") or 0)
        currency = link_data.get("currency") or payment_data.get("currency") or case.currency
        if paid_amount < case.risk_amount_minor or currency != case.currency:
            action.status = ActionStatus.FAILED.value
            action.last_error = "PAYMENT_LINK_AMOUNT_OR_CURRENCY_MISMATCH"
            return
        case.state = CaseState.RECOVERED_VERIFIED.value
        case.recovered_amount_minor = case.risk_amount_minor
        case.recovered_at = _event_time(event)
        case.state_version += 1
        action.status = ActionStatus.SUCCEEDED.value
        append_audit(
            db,
            merchant_id=event.merchant_id,
            aggregate_type="recovery_case",
            aggregate_id=case.id,
            event_kind="RECOVERY_VERIFIED",
            actor_type="webhook",
            actor_id=event.provider_event_id,
            decision={
                "payment_id": payment_data.get("id"),
                "payment_link_id": link_id,
                "amount_minor": case.recovered_amount_minor,
            },
        )
    elif event.event_type in {"payment_link.cancelled", "payment_link.expired"}:
        action.status = ActionStatus.FAILED.value
        action.last_error = event.event_type.upper().replace(".", "_")


def process_webhook_event(
    db: Session,
    webhook_event_id: uuid.UUID,
    *,
    settings: Settings | None = None,
) -> None:
    event = db.get(WebhookEvent, webhook_event_id)
    if event is None or event.processed_at is not None:
        return
    if event.event_type not in SUPPORTED_EVENTS:
        event.processed_at = datetime.now(UTC)
        return
    if event.event_type.startswith("payment_link."):
        _handle_payment_link_event(db, event)
        event.processed_at = datetime.now(UTC)
        return

    customer, subscription, subscription_data, payment_data = _upsert_subscription_context(
        db, event
    )
    _record_payment_event(db, webhook=event, subscription=subscription, payment_data=payment_data)
    stale = (
        subscription.provider_updated_at is not None
        and _event_time(event) < subscription.provider_updated_at
    )
    if event.event_type in {"subscription.pending", "subscription.halted"} and not stale:
        _handle_at_risk(
            db,
            event,
            customer,
            subscription,
            subscription_data,
            payment_data,
            settings,
        )
    elif event.event_type == "subscription.charged":
        _handle_charged(db, event, subscription, payment_data)
    event.processed_at = datetime.now(UTC)
