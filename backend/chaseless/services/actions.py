from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from chaseless.core.contact_crypto import ContactEncryptionError
from chaseless.core.settings import Settings
from chaseless.db.models import (
    CandidateAction,
    ConversationEvent,
    Customer,
    MerchantPolicyVersion,
    RecoveryAction,
    RecoveryCase,
    Subscription,
)
from chaseless.domain.enums import ActionStatus, ActionType, CaseState, PolicyVerdict
from chaseless.domain.policy import PolicyConfig, evaluate_candidate
from chaseless.domain.types import ActionCandidate, RecoveryContext
from chaseless.integrations.llm.message_composer import (
    ComposedRecoveryCopy,
    compose_recovery_copy,
)
from chaseless.integrations.messaging.client import (
    MessagingConfigurationError,
    MessagingResult,
    send_email_message,
    send_sms_message,
    send_whatsapp_message,
)
from chaseless.integrations.razorpay.client import RazorpayClient
from chaseless.integrations.voice.client import VoiceConfigurationError, place_twilio_test_call
from chaseless.services.audit import append_audit
from chaseless.services.contacts import email_endpoint, phone_endpoint, whatsapp_endpoint


def _context(case: RecoveryCase, customer: Customer, subscription: Subscription) -> RecoveryContext:
    evidence = case.diagnosis.get("evidence", []) if case.diagnosis else []
    failure_code = next(
        (item.split("=", 1)[1] for item in evidence if item.startswith("provider_failure_code=")),
        None,
    )
    return RecoveryContext(
        case_id=str(case.id),
        amount_minor=case.risk_amount_minor,
        currency=case.currency,
        subscription_status=subscription.status,
        failure_code=failure_code,
        contacts_24h=customer.contacts_24h,
        contacts_7d=customer.contacts_7d,
        opted_out=customer.opted_out,
        promise_to_pay_at=customer.promise_to_pay_at,
        intervention_count=case.contact_count,
        consent_channels={key for key, value in customer.consent.items() if value},
    )


def _authorize_again(db: Session, action: RecoveryAction) -> tuple[bool, list[str]]:
    case = db.get(RecoveryCase, action.case_id)
    candidate_row = db.get(CandidateAction, action.candidate_action_id)
    if case is None or candidate_row is None:
        return False, ["ACTION_CONTEXT_MISSING"]
    if case.state in {
        CaseState.RECOVERED_VERIFIED.value,
        CaseState.STOPPED.value,
        CaseState.EXHAUSTED.value,
    }:
        return False, ["CASE_TERMINAL"]
    customer = db.get(Customer, case.customer_id)
    subscription = db.get(Subscription, case.subscription_id)
    if customer is None or subscription is None:
        return False, ["ACTION_CONTEXT_MISSING"]
    policy_row = (
        db.query(MerchantPolicyVersion)
        .filter(MerchantPolicyVersion.merchant_id == case.merchant_id)
        .order_by(MerchantPolicyVersion.version.desc())
        .first()
    )
    if policy_row is None:
        return False, ["POLICY_MISSING"]
    candidate = ActionCandidate(
        case_id=str(case.id),
        action_type=ActionType(candidate_row.action_type),
        probability_action=candidate_row.probability_action,
        probability_natural=candidate_row.probability_natural,
        action_cost_minor=candidate_row.action_cost_minor,
        fatigue_penalty_minor=candidate_row.fatigue_penalty_minor,
        risk_penalty_minor=candidate_row.risk_penalty_minor,
        eirv_minor=candidate_row.eirv_minor,
        contact_units=candidate_row.contact_units,
    )
    verdict = evaluate_candidate(
        candidate,
        _context(case, customer, subscription),
        PolicyConfig.model_validate(policy_row.rules_json),
    )
    allowed = verdict.eligible and verdict.policy_verdict in {
        PolicyVerdict.ALLOW,
        PolicyVerdict.REQUIRE_APPROVAL,
    }
    return allowed, verdict.policy_reasons


def _nudge_channel_order(customer: Customer) -> list[str]:
    """Return a bounded, deterministic channel order; never fan out across channels."""
    configured = customer.contact_preferences.get("recovery_channel_order", [])
    allowed = {"whatsapp", "sms", "email"}
    if isinstance(configured, list):
        ordered = [item for item in configured if isinstance(item, str) and item in allowed]
        if ordered:
            return list(dict.fromkeys(ordered))
    return ["whatsapp", "sms", "email"]


def _deliver_nudge(
    customer: Customer, settings: Settings, *, copy: ComposedRecoveryCopy
) -> tuple[str, MessagingResult]:
    unavailable: list[str] = []
    for channel in _nudge_channel_order(customer):
        try:
            if channel == "whatsapp":
                return channel, send_whatsapp_message(
                    settings,
                    recipient_e164=whatsapp_endpoint(customer, settings),
                    body=copy.whatsapp,
                )
            if channel == "sms":
                return channel, send_sms_message(
                    settings,
                    recipient_e164=phone_endpoint(customer, settings, channel="sms"),
                    body=copy.sms,
                )
            return channel, send_email_message(
                settings,
                recipient=email_endpoint(customer, settings),
                subject=copy.email_subject,
                body=copy.email_body,
            )
        except (ContactEncryptionError, MessagingConfigurationError) as exc:
            unavailable.append(f"{channel}:{exc}")
    raise MessagingConfigurationError(
        "No consented delivery channel is available: " + "; ".join(unavailable)
    )


def _diagnosis_label(case: RecoveryCase) -> str:
    if not case.diagnosis:
        return "UNKNOWN"
    evidence = case.diagnosis.get("evidence", [])
    provider_code = next(
        (
            item.split("=", 1)[1]
            for item in evidence
            if isinstance(item, str) and item.startswith("provider_failure_code=")
        ),
        None,
    )
    return str(provider_code or case.diagnosis.get("failure_class", "UNKNOWN"))


def _create_notified_payment_link(
    settings: Settings,
    *,
    case: RecoveryCase,
    action: RecoveryAction,
    customer: Customer,
    now: datetime,
) -> dict[str, object]:
    """Create a Test Mode link and let Razorpay deliver its own SMS/email notification."""
    contact = phone_endpoint(customer, settings, channel="sms")
    email = email_endpoint(customer, settings)
    return RazorpayClient(settings).create_payment_link(
        amount_minor=case.risk_amount_minor,
        currency=case.currency,
        reference_id=f"act_{action.id.hex[:32]}",
        case_id=str(case.id),
        action_id=str(action.id),
        expire_by=now + timedelta(days=7),
        customer_name=customer.display_name,
        customer_contact=contact,
        customer_email=email,
        notify_sms=True,
        notify_email=True,
    )


def execute_action(db: Session, action_id: uuid.UUID, settings: Settings) -> None:
    action = db.get(RecoveryAction, action_id)
    if action is None or action.status in {
        ActionStatus.SUCCEEDED.value,
        ActionStatus.CANCELLED.value,
        ActionStatus.SKIPPED.value,
    }:
        return
    if action.requires_approval and action.status == ActionStatus.APPROVAL_REQUIRED.value:
        return
    allowed, reasons = _authorize_again(db, action)
    case = db.get(RecoveryCase, action.case_id)
    if case is None:
        return
    if not allowed:
        action.status = ActionStatus.SKIPPED.value
        action.last_error = ",".join(reasons)
        append_audit(
            db,
            merchant_id=case.merchant_id,
            aggregate_type="recovery_action",
            aggregate_id=action.id,
            event_kind="ACTION_BLOCKED_AT_EXECUTION",
            actor_type="policy",
            actor_id="policy-engine-v1",
            decision={"reasons": reasons},
        )
        return

    action.status = ActionStatus.EXECUTING.value
    action_type = ActionType(action.action_type)
    now = datetime.now(UTC)
    if action_type in {ActionType.WAIT, ActionType.NATIVE_RETRY_WAIT}:
        case.state = CaseState.WAITING.value
        action.result = {**dict(action.result or {}), "observation": "waiting_for_trusted_event"}
    elif action_type == ActionType.STOP:
        case.state = CaseState.STOPPED.value
        case.terminal_reason = reasons[0] if reasons else "POLICY_STOP"
        action.result = {**dict(action.result or {}), "terminal_reason": case.terminal_reason}
    elif action_type == ActionType.HUMAN_ESCALATE:
        case.state = CaseState.HUMAN_REVIEW.value
        action.result = {**dict(action.result or {}), "review_task": f"review-{action.id.hex[:12]}"}
    elif action_type in {ActionType.NUDGE, ActionType.UPDATE_PAYMENT_METHOD}:
        customer = db.get(Customer, case.customer_id)
        if customer is None:
            raise RuntimeError("Action customer is missing")
        copy = compose_recovery_copy(
            settings,
            amount_minor=case.risk_amount_minor,
            currency=case.currency,
            diagnosis=_diagnosis_label(case),
            action_type=action_type,
        )
        try:
            channel, delivery = _deliver_nudge(customer, settings, copy=copy)
        except (ContactEncryptionError, MessagingConfigurationError) as exc:
            try:
                response = _create_notified_payment_link(
                    settings, case=case, action=action, customer=customer, now=now
                )
            except Exception:
                action.status = ActionStatus.SKIPPED.value
                action.last_error = (
                    "Delivery is unavailable on the current Twilio trial. "
                    "Activate a registered WhatsApp template, upgraded Twilio messaging, "
                    "or SMTP/SendGrid."
                )
                append_audit(
                    db,
                    merchant_id=case.merchant_id,
                    aggregate_type="recovery_action",
                    aggregate_id=action.id,
                    event_kind="ACTION_BLOCKED_AT_DELIVERY",
                    actor_type="policy",
                    actor_id="contact-delivery-guardrail-v1",
                    decision={"reason": str(exc), "channel": "nudge"},
                )
                return
            channel = "sms+email"
            delivery = MessagingResult(
                provider="razorpay-native-notify",
                reference=str(response["id"]),
                delivery_state="queued",
            )
            copy = ComposedRecoveryCopy(
                **{
                    **copy.__dict__,
                    "sms": "Razorpay queued a secure Payment Link notification by SMS.",
                    "email_body": "Razorpay queued a secure Payment Link notification by email.",
                }
            )
            action.result = {
                **dict(action.result or {}),
                "payment_link_id": response["id"],
                "short_url": response["short_url"],
                "delivery_warning": (
                    "Twilio custom messages are restricted on this trial, so Razorpay sent the "
                    "secure payment link by SMS and email instead."
                ),
            }
        customer.contacts_24h += 1
        customer.contacts_7d += 1
        case.contact_count += 1
        case.state = CaseState.OBSERVING.value
        if delivery.provider != "mock":
            action.provider_reference = delivery.reference
        outbound_message = (
            copy.whatsapp
            if channel == "whatsapp"
            else copy.sms
            if channel in {"sms", "sms+email"}
            else copy.email_body
        )
        db.add(
            ConversationEvent(
                case_id=case.id,
                channel=channel,
                direction="OUTBOUND",
                content_redacted=outbound_message,
                extracted={
                    "action_id": str(action.id),
                    "provider": delivery.provider,
                    "provider_reference": delivery.reference,
                    "delivery_state": delivery.delivery_state,
                    "email_subject": copy.email_subject if channel == "email" else None,
                },
                extractor_version="delivery-v1",
            )
        )
        action.result = {
            **dict(action.result or {}),
            "channel": channel,
            "provider": delivery.provider,
            "provider_reference": delivery.reference,
            "delivery_state": delivery.delivery_state,
            "template": "payment_method_update_v1"
            if action_type == ActionType.UPDATE_PAYMENT_METHOD
            else "payment_reminder_v1",
            "bounded_fields": True,
            "copy_source": copy.source,
            "message": outbound_message,
            "email_subject": copy.email_subject if channel == "email" else None,
        }
    elif action_type == ActionType.PAYMENT_LINK:
        customer = db.get(Customer, case.customer_id)
        if customer is None:
            raise RuntimeError("Action customer is missing")
        try:
            response = _create_notified_payment_link(
                settings, case=case, action=action, customer=customer, now=now
            )
        except Exception as exc:
            action.status = ActionStatus.FAILED.value
            action.last_error = f"PAYMENT_LINK_FAILED: {type(exc).__name__}"
            append_audit(
                db,
                merchant_id=case.merchant_id,
                aggregate_type="recovery_action",
                aggregate_id=action.id,
                event_kind="ACTION_PROVIDER_FAILED",
                actor_type="provider",
                actor_id="razorpay-test",
                decision={"reason": action.last_error},
            )
            return
        action.provider_reference = response["id"]
        copy = compose_recovery_copy(
            settings,
            amount_minor=case.risk_amount_minor,
            currency=case.currency,
            diagnosis=_diagnosis_label(case),
            action_type=ActionType.PAYMENT_LINK,
            payment_url=response["short_url"],
        )
        try:
            channel, delivery = _deliver_nudge(customer, settings, copy=copy)
        except (ContactEncryptionError, MessagingConfigurationError) as exc:
            action.result = {
                **dict(action.result or {}),
                "payment_link_id": response["id"],
                "short_url": response["short_url"],
                "status": response["status"],
                "delivery_channel": "sms+email",
                "delivery_provider": "razorpay-native-notify",
                "delivery_state": "queued",
                "delivery_warning": (
                    "Twilio custom messages are restricted on this trial, so Razorpay sent the "
                    "secure payment link by SMS and email instead."
                ),
            }
            customer.contacts_24h += 1
            customer.contacts_7d += 1
            case.contact_count += 1
            case.state = CaseState.OBSERVING.value
            append_audit(
                db,
                merchant_id=case.merchant_id,
                aggregate_type="recovery_action",
                aggregate_id=action.id,
                event_kind="ACTION_DELIVERY_FALLBACK",
                actor_type="provider",
                actor_id="razorpay-native-notify",
                decision={"twilio_reason": str(exc), "channels": ["sms", "email"]},
            )
            action.status = ActionStatus.SUCCEEDED.value
            action.executed_at = now
            case.state_version += 1
            return
        customer.contacts_24h += 1
        customer.contacts_7d += 1
        case.contact_count += 1
        outbound_message = (
            copy.whatsapp
            if channel == "whatsapp"
            else copy.sms
            if channel == "sms"
            else copy.email_body
        )
        db.add(
            ConversationEvent(
                case_id=case.id,
                channel=channel,
                direction="OUTBOUND",
                content_redacted=outbound_message,
                extracted={
                    "action_id": str(action.id),
                    "provider": delivery.provider,
                    "provider_reference": delivery.reference,
                    "delivery_state": delivery.delivery_state,
                    "email_subject": copy.email_subject if channel == "email" else None,
                },
                extractor_version="delivery-v1",
            )
        )
        action.result = {
            **dict(action.result or {}),
            "payment_link_id": response["id"],
            "short_url": response["short_url"],
            "status": response["status"],
            "delivery_channel": channel,
            "delivery_provider": delivery.provider,
            "delivery_reference": delivery.reference,
            "copy_source": copy.source,
            "message": outbound_message,
            "email_subject": copy.email_subject if channel == "email" else None,
        }
        case.state = CaseState.OBSERVING.value
    elif action_type == ActionType.VOICE_AGENT:
        customer = db.get(Customer, case.customer_id)
        if customer is None:
            raise RuntimeError("Action customer is missing")
        copy = compose_recovery_copy(
            settings,
            amount_minor=case.risk_amount_minor,
            currency=case.currency,
            diagnosis=_diagnosis_label(case),
            action_type=ActionType.VOICE_AGENT,
        )
        voice_script = (
            f"{copy.voice} Please say when you expect to pay. "
            "You may say tomorrow, or press 1 for tomorrow, 2 for today, "
            "or 3 to speak with a person."
        )
        try:
            recipient = phone_endpoint(customer, settings, channel="voice")
            if settings.voice_provider == "mock":
                provider = "mock"
                reference = "mock-voice"
                delivery_state = "mocked"
            elif settings.voice_provider == "twilio":
                call = place_twilio_test_call(
                    settings,
                    text=voice_script,
                    recipient_e164=recipient,
                    action_id=action.id,
                )
                provider = "twilio-voice"
                reference = call.sid
                delivery_state = call.status
            else:
                raise VoiceConfigurationError("Unsupported voice provider")
        except (ContactEncryptionError, VoiceConfigurationError, httpx.HTTPError) as exc:
            action.status = ActionStatus.FAILED.value
            action.last_error = "VOICE_DELIVERY_FAILED: " + (str(exc) or type(exc).__name__)
            append_audit(
                db,
                merchant_id=case.merchant_id,
                aggregate_type="recovery_action",
                aggregate_id=action.id,
                event_kind="ACTION_PROVIDER_FAILED",
                actor_type="provider",
                actor_id="twilio-voice",
                decision={"reason": action.last_error},
            )
            return
        customer.contacts_24h += 1
        customer.contacts_7d += 1
        case.contact_count += 1
        case.state = CaseState.OBSERVING.value
        if provider != "mock":
            action.provider_reference = reference
        db.add(
            ConversationEvent(
                case_id=case.id,
                channel="voice",
                direction="OUTBOUND",
                content_redacted=voice_script,
                extracted={
                    "action_id": str(action.id),
                    "provider": provider,
                    "provider_reference": reference,
                    "delivery_state": delivery_state,
                    "interactive": True,
                },
                extractor_version="voice-ivr-v1",
            )
        )
        action.result = {
            **dict(action.result or {}),
            "channel": "voice",
            "provider": provider,
            "provider_reference": reference,
            "delivery_state": delivery_state,
            "interactive": True,
            "response_capture": "speech_or_dtmf",
            "bounded_fields": True,
            "copy_source": copy.source,
            "message": voice_script,
        }
    else:
        raise RuntimeError(f"Unsupported P0 action: {action_type.value}")
    action.status = ActionStatus.SUCCEEDED.value
    action.executed_at = now
    case.state_version += 1
    append_audit(
        db,
        merchant_id=case.merchant_id,
        aggregate_type="recovery_action",
        aggregate_id=action.id,
        event_kind="ACTION_EXECUTED",
        actor_type="executor",
        actor_id="action-executor-v1",
        decision={"action_type": action.action_type, "result": action.result},
    )
