from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from chaseless.core.settings import Settings
from chaseless.db.models import (
    AuditEvent,
    ConversationEvent,
    Customer,
    RecoveryCase,
    Subscription,
)
from chaseless.domain.enums import CaseState
from chaseless.services.audit import append_audit
from chaseless.services.event_processor import process_webhook_event
from chaseless.services.webhooks import ingest_razorpay_webhook

SIMULATION_PREFIX = "DEMO_SIMULATION_"


def _events(db: Session, case_id: uuid.UUID) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.aggregate_type == "recovery_case",
            AuditEvent.aggregate_id == case_id,
            AuditEvent.event_kind.like(f"{SIMULATION_PREFIX}%"),
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
        .all()
    )


def _simulation_events(events: list[AuditEvent], simulation_id: uuid.UUID) -> list[AuditEvent]:
    expected = str(simulation_id)
    return [event for event in events if event.decision.get("simulation_id") == expected]


def _state(
    db: Session,
    case: RecoveryCase,
    *,
    simulation_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    events = _events(db, case.id)
    starts = [event for event in events if event.event_kind == "DEMO_SIMULATION_STARTED"]
    if simulation_id is None:
        if not starts:
            return None
        simulation_id = uuid.UUID(str(starts[-1].decision["simulation_id"]))
    selected = _simulation_events(events, simulation_id)
    if not selected:
        return None
    completed = [event for event in selected if int(event.decision.get("progress", 0)) > 0]
    progress = max((int(event.decision.get("progress", 0)) for event in selected), default=0)
    customer = db.get(Customer, case.customer_id)
    outcome = "in_progress"
    if progress >= 7:
        outcome = (
            "recovered"
            if case.state == CaseState.RECOVERED_VERIFIED.value
            else "stopped"
        )
    return {
        "simulation_id": simulation_id,
        "case_id": case.id,
        "progress": progress,
        "status": "COMPLETED" if progress >= 7 else "RUNNING",
        "outcome": outcome,
        "completed_steps": [
            {
                "progress": int(event.decision["progress"]),
                "event_kind": event.event_kind,
                "label": event.decision.get("label", event.event_kind),
                "detail": event.decision.get("detail", ""),
                "actor": event.actor_id,
                "created_at": event.created_at,
            }
            for event in completed
        ],
        "promise_to_pay_at": customer.promise_to_pay_at if customer else None,
        "recovered_amount_minor": case.recovered_amount_minor,
        "payment_url": next(
            (
                str(event.decision["payment_url"])
                for event in reversed(selected)
                if event.decision.get("payment_url")
            ),
            None,
        ),
        "last_event_at": selected[-1].created_at,
    }


def latest_simulation(db: Session, case: RecoveryCase) -> dict[str, Any] | None:
    return _state(db, case)


def start_simulation(
    db: Session,
    case: RecoveryCase,
    *,
    command_id: uuid.UUID,
) -> dict[str, Any]:
    events = _events(db, case.id)
    duplicate = next(
        (event for event in events if event.decision.get("command_id") == str(command_id)),
        None,
    )
    if duplicate is not None:
        duplicate_id = uuid.UUID(str(duplicate.decision["simulation_id"]))
        state = _state(db, case, simulation_id=duplicate_id)
        if state is None:
            raise RuntimeError("Simulation audit state is incomplete")
        return state

    simulation_id = uuid.uuid4()
    if case.state == CaseState.RECOVERED_VERIFIED.value:
        case.state = CaseState.DIAGNOSED.value
        case.recovered_amount_minor = 0
        case.recovered_at = None
        case.state_version += 1
    append_audit(
        db,
        merchant_id=case.merchant_id,
        aggregate_type="recovery_case",
        aggregate_id=case.id,
        event_kind="DEMO_SIMULATION_STARTED",
        actor_type="internal_user",
        actor_id="demo-operator",
        decision={
            "simulation_id": str(simulation_id),
            "command_id": str(command_id),
            "progress": 0,
            "labelled_simulation": True,
        },
    )
    append_audit(
        db,
        merchant_id=case.merchant_id,
        aggregate_type="recovery_case",
        aggregate_id=case.id,
        event_kind="DEMO_SIMULATION_PAYMENT_FAILURE_DETECTED",
        actor_type="webhook",
        actor_id="razorpay-test-signed-fixture",
        decision={
            "simulation_id": str(simulation_id),
            "command_id": str(command_id),
            "progress": 1,
            "label": "Payment failure detected",
            "detail": "Signed at-risk event accepted and deduplicated",
            "labelled_simulation": True,
        },
    )
    db.commit()
    state = _state(db, case, simulation_id=simulation_id)
    if state is None:
        raise RuntimeError("Simulation could not be started")
    return state


def _synthetic_charged_webhook(
    db: Session,
    case: RecoveryCase,
    subscription: Subscription,
    customer: Customer,
    simulation_id: uuid.UUID,
    settings: Settings,
) -> str:
    now = datetime.now(UTC)
    provider_event_id = f"evt_demo_{simulation_id.hex}_paid"
    payload = {
        "event": "subscription.charged",
        "created_at": int(now.timestamp()),
        "payload": {
            "subscription": {
                "entity": {
                    "id": subscription.razorpay_subscription_id,
                    "customer_id": customer.provider_customer_id,
                    "plan_id": subscription.razorpay_plan_id,
                    "status": "active",
                    "current_start": int(now.timestamp()),
                    "notes": {"demo_source": "synthetic_fixture"},
                }
            },
            "payment": {
                "entity": {
                    "id": f"pay_demo_{simulation_id.hex}",
                    "invoice_id": f"inv_demo_{simulation_id.hex[:16]}",
                    "status": "captured",
                    "amount": case.risk_amount_minor,
                    "currency": case.currency,
                }
            },
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    secret = settings.razorpay_webhook_secret
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    accepted = ingest_razorpay_webhook(
        db,
        raw_body=raw,
        signature=signature,
        provider_event_id=provider_event_id,
        current_secret=secret,
        previous_secret=settings.razorpay_previous_webhook_secret,
    )
    if accepted.event_id is not None:
        process_webhook_event(db, accepted.event_id, settings=settings)
    return provider_event_id


def advance_simulation(
    db: Session,
    case: RecoveryCase,
    *,
    simulation_id: uuid.UUID,
    command_id: uuid.UUID,
    expected_progress: int,
    promise_to_pay: bool,
    settings: Settings,
) -> dict[str, Any]:
    events = _simulation_events(_events(db, case.id), simulation_id)
    if not events:
        raise ValueError("Simulation not found")
    duplicate = next(
        (event for event in events if event.decision.get("command_id") == str(command_id)),
        None,
    )
    if duplicate is not None:
        state = _state(db, case, simulation_id=simulation_id)
        if state is None:
            raise RuntimeError("Simulation audit state is incomplete")
        return state
    progress = max(int(event.decision.get("progress", 0)) for event in events)
    if progress >= 7:
        state = _state(db, case, simulation_id=simulation_id)
        if state is None:
            raise RuntimeError("Simulation audit state is incomplete")
        return state
    if progress != expected_progress:
        raise ValueError(f"Simulation moved from step {expected_progress} to {progress}")

    customer = db.get(Customer, case.customer_id)
    subscription = db.get(Subscription, case.subscription_id)
    if customer is None or subscription is None:
        raise RuntimeError("Recovery case context is incomplete")
    preferences = customer.contact_preferences or {}
    action = str(preferences.get("recommended_action", "WAIT"))
    next_progress = progress + 1
    actor_type = "system"
    actor_id = "recovery-simulator-v1"
    event_kind = "DEMO_SIMULATION_STEP_COMPLETED"
    label = "Recovery step completed"
    detail = ""
    extra_decision: dict[str, Any] = {}

    if next_progress == 2:
        event_kind = "DEMO_SIMULATION_FAILURE_DIAGNOSED"
        label = "Failure diagnosed"
        detail = str(case.diagnosis.get("failure_class", "UNKNOWN"))
        case.state = CaseState.DIAGNOSED.value
    elif next_progress == 3:
        event_kind = "DEMO_SIMULATION_DECISION_AUTHORIZED"
        actor_type = "policy"
        actor_id = "policy-engine-v1"
        label = "Recovery decision authorized"
        detail = f"{action} passed profitability and policy checks"
        case.state = CaseState.PLANNED.value
    elif next_progress == 4 and action == "STOP":
        next_progress = 7
        event_kind = "DEMO_SIMULATION_POLICY_STOPPED"
        actor_type = "policy"
        actor_id = "policy-engine-v1"
        label = "Automation stopped safely"
        detail = "Stopping rule blocked further customer contact"
        case.state = CaseState.STOPPED.value
        case.terminal_reason = "DEMO_POLICY_STOP"
    elif next_progress == 4:
        event_kind = "DEMO_SIMULATION_ACTION_EXECUTED"
        actor_type = "executor"
        actor_id = "mock-provider-bounded"
        label = "Bounded recovery action executed"
        detail = f"{action} executed without contacting a real customer"
        case.state = CaseState.OBSERVING.value
        case.contact_count += 0 if action in {"WAIT", "NATIVE_RETRY_WAIT"} else 1
        if action == "PAYMENT_LINK":
            extra_decision["payment_url"] = (
                f"/pay/{case.id}?simulation_id={simulation_id}"
            )
            detail = "Razorpay-style Test Payment Link created for this recovery episode"
        if action == "VOICE_AGENT":
            db.add(
                ConversationEvent(
                    case_id=case.id,
                    channel="voice",
                    direction="OUTBOUND",
                    content_redacted=(
                        "Namaste. This is a payment reminder for your overdue balance. "
                        "Would you like a secure payment link or more time to pay?"
                    ),
                    extracted={
                        "simulation_id": str(simulation_id),
                        "language": "hinglish",
                        "provider": "mock-voice",
                    },
                    extractor_version="demo-voice-v1",
                )
            )
    elif next_progress == 5:
        if promise_to_pay:
            customer.promise_to_pay_at = datetime.now(UTC) + timedelta(days=3)
            event_kind = "DEMO_SIMULATION_PROMISE_TO_PAY_CAPTURED"
            actor_type = "customer"
            actor_id = "synthetic-customer-response"
            label = "Promise to pay captured"
            detail = "Customer committed to pay within three days; grace period activated"
            db.add(
                ConversationEvent(
                    case_id=case.id,
                    channel="voice" if action == "VOICE_AGENT" else "whatsapp",
                    direction="INBOUND",
                    content_redacted="I will pay on Friday.",
                    extracted={
                        "simulation_id": str(simulation_id),
                        "intent": "PROMISE_TO_PAY",
                        "commitment_date": customer.promise_to_pay_at.isoformat(),
                        "confidence": 0.96,
                    },
                    extractor_version="promise-extractor-v1",
                )
            )
        else:
            event_kind = "DEMO_SIMULATION_WAIT_FAST_FORWARDED"
            actor_type = "clock"
            actor_id = "demo-time-controller"
            label = "Observation window fast-forwarded"
            detail = "Twelve hours elapsed without an additional customer contact"
        case.state = CaseState.WAITING.value
    elif next_progress == 6:
        provider_event_id = _synthetic_charged_webhook(
            db, case, subscription, customer, simulation_id, settings
        )
        event_kind = "DEMO_SIMULATION_PAYMENT_VERIFIED"
        actor_type = "webhook"
        actor_id = provider_event_id
        label = "Payment verified from signed webhook"
        detail = f"Amount and currency matched {case.currency} {case.risk_amount_minor}"
        customer.promise_to_pay_at = None
    elif next_progress == 7:
        event_kind = "DEMO_SIMULATION_REVENUE_ATTRIBUTED"
        actor_type = "system"
        actor_id = "attribution-engine-v1"
        label = "Revenue recovered and attributed"
        detail = f"{case.risk_amount_minor} minor units attributed to this episode"

    case.state_version += 1
    append_audit(
        db,
        merchant_id=case.merchant_id,
        aggregate_type="recovery_case",
        aggregate_id=case.id,
        event_kind=event_kind,
        actor_type=actor_type,
        actor_id=actor_id,
        decision={
            "simulation_id": str(simulation_id),
            "command_id": str(command_id),
            "progress": next_progress,
            "label": label,
            "detail": detail,
            "action": action,
            "labelled_simulation": True,
            **extra_decision,
        },
    )
    db.commit()
    state = _state(db, case, simulation_id=simulation_id)
    if state is None:
        raise RuntimeError("Simulation state could not be read")
    return state
