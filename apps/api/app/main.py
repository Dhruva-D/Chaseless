from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, cast
from urllib.parse import parse_qs, urlencode
from xml.sax.saxutils import escape

import structlog
from chaseless.core.contact_crypto import ContactEncryptionError
from chaseless.core.logging import configure_logging
from chaseless.core.settings import get_settings
from chaseless.db.models import (
    AuditEvent,
    ConversationEvent,
    Customer,
    OutboxEvent,
    RecoveryAction,
    RecoveryCase,
    RecoveryRun,
    Subscription,
    WebhookEvent,
)
from chaseless.db.session import get_db
from chaseless.domain.enums import CaseState
from chaseless.integrations.llm.voice_dialogue import compose_voice_reply
from chaseless.integrations.voice.client import (
    VoiceConfigurationError,
    build_voice_audio_url,
    decode_voice_audio_message,
    decode_voice_twiml_message,
    synthesize_sarvam_speech,
    verify_interactive_voice_signature,
)
from chaseless.integrations.voice.conversation import interpret_voice_response
from chaseless.services.audit import append_audit
from chaseless.services.case_automation import (
    execute_scheduled_case_action,
    schedule_voice_payment_link,
    start_case_automation,
)
from chaseless.services.contacts import (
    store_email_endpoint,
    store_phone_endpoint,
    store_whatsapp_endpoint,
)
from chaseless.services.demo_simulation import (
    advance_simulation,
    latest_simulation,
    start_simulation,
)
from chaseless.services.razorpay_test_import import (
    import_selected_test_payments,
    preview_failed_subscription_payments,
)
from chaseless.services.recovery import (
    approve_run,
    decide_action_approval,
    execute_run,
    preview_recovery_run,
)
from chaseless.services.webhooks import (
    WebhookRejected,
    active_merchant,
    ingest_razorpay_webhook,
)
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from redis import Redis
from sqlalchemy import or_, text
from sqlalchemy.orm import Session
from starlette.responses import Response

from apps.api.app.schemas import (
    ApprovalRequest,
    CaseAutomationStartRequest,
    CaseAutomationState,
    CustomerContactResponse,
    DashboardSummary,
    EmailEndpointRequest,
    HealthResponse,
    OperationsHealth,
    PhoneEndpointRequest,
    RazorpayTestImportCandidate,
    RazorpayTestImportRequest,
    RazorpayTestImportResult,
    RecoveryCaseDetail,
    RecoveryCaseListItem,
    RecoveryRunPreviewRequest,
    RecoveryRunResponse,
    SimulationAdvanceRequest,
    SimulationStartRequest,
    SimulationState,
    WebhookAccepted,
    WebhookEventListItem,
    WhatsAppEndpointRequest,
)
from apps.api.app.security import require_internal_token
from scripts.configure_demo_contacts import main as configure_demo_contacts
from scripts.seed_demo import main as seed_demo

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("application_started", version=settings.app_version, environment=settings.app_env)
    yield
    logger.info("application_stopped")


app = FastAPI(
    title="ChaseLess API",
    version=settings.app_version,
    description="Policy-bounded recurring revenue recovery",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
    finally:
        structlog.contextvars.clear_contextvars()


@app.get("/health/live", response_model=HealthResponse)
def live() -> HealthResponse:
    return HealthResponse(status="ok", version=settings.app_version)


@app.get("/health/ready", response_model=HealthResponse)
def ready(db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return HealthResponse(status="ready", version=settings.app_version)


@app.api_route("/api/v1/voice/twiml", methods=["GET", "POST"])
def voice_twiml(
    message: str = Query(),
    signature: str = Query(),
    action_id: uuid.UUID | None = Query(default=None),
) -> Response:
    """Serve signed, bounded voice instructions without exposing an open text-to-call endpoint."""
    if action_id is None:
        try:
            text_value = decode_voice_twiml_message(settings, message=message, signature=signature)
        except VoiceConfigurationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Say voice="Polly.Aditi" language="en-IN">'
            f"{escape(text_value)}</Say></Response>"
        )
        return Response(content=document, media_type="application/xml")
    if not verify_interactive_voice_signature(settings, action_id=action_id, signature=signature):
        raise HTTPException(status_code=401, detail="Voice IVR signature is invalid")
    callback_query = urlencode({"action_id": str(action_id), "signature": signature})
    callback_url = f"{settings.api_public_url.rstrip('/')}/api/v1/voice/response?{callback_query}"
    question = (
        "Please say when you expect to pay. You can say tomorrow, or press 1 for tomorrow, "
        "2 for today, or 3 to speak with a person."
    )
    spoken_text = f"{message} {question}"
    audio_url = build_voice_audio_url(settings, text=spoken_text)
    no_response_url = build_voice_audio_url(
        settings, text="We did not receive a response. Thank you."
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        '<Gather input="speech dtmf" timeout="8" speechTimeout="auto" '
        f'action="{escape(callback_url)}" method="POST">'
        f"<Play>{escape(audio_url)}</Play></Gather>"
        f"<Play>{escape(no_response_url)}</Play>"
        "</Response>"
    )
    return Response(content=document, media_type="application/xml")


@app.get("/api/v1/voice/audio")
def voice_audio(message: str = Query(), signature: str = Query()) -> Response:
    """Synthesize a short signed voice prompt with Sarvam Bulbul for telephone playback."""
    try:
        text_value = decode_voice_audio_message(settings, message=message, signature=signature)
        speech = synthesize_sarvam_speech(settings, text=text_value)
    except VoiceConfigurationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return Response(
        content=speech.audio_bytes,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.post("/api/v1/voice/response")
async def voice_response(
    request: Request,
    background_tasks: BackgroundTasks,
    action_id: uuid.UUID = Query(),
    signature: str = Query(),
    db: Session = Depends(get_db),
) -> Response:
    """Capture a Twilio IVR response and apply only deterministic follow-up states."""
    if not verify_interactive_voice_signature(settings, action_id=action_id, signature=signature):
        raise HTTPException(status_code=401, detail="Voice IVR signature is invalid")
    action = db.get(RecoveryAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Recovery action not found")
    case = db.get(RecoveryCase, action.case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")
    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    speech = form.get("SpeechResult", [""])[0][:500]
    digits = form.get("Digits", [""])[0][:8]
    call_sid = form.get("CallSid", [""])[0][:100]
    existing_response = dict(action.result or {}).get("voice_response")
    if (
        isinstance(existing_response, dict)
        and call_sid
        and existing_response.get("call_sid") == call_sid
    ):
        intent = str(existing_response.get("intent", "UNKNOWN"))
        acknowledgement = {
            "PAY_ON_DATE": "Thank you. We have already recorded your expected payment date.",
            "PAY_TODAY": "Thank you. We have already recorded that you expect to pay today.",
            "NEEDS_HELP": "Thank you. We have already requested a human follow up.",
            "DECLINES_PAYMENT": "Thank you. We have already recorded your response.",
            "UNKNOWN": "Thank you. We have already recorded your response for review.",
        }.get(intent, "Thank you. We have already recorded your response for review.")
        document = (
            '<?xml version="1.0" encoding="UTF-8"?><Response>'
            '<Say voice="Polly.Aditi" language="en-IN">'
            f"{escape(acknowledgement)}</Say></Response>"
        )
        return Response(content=document, media_type="application/xml")
    outcome = interpret_voice_response(speech=speech, digits=digits, today=datetime.now().date())
    commitment_date = (
        outcome.commitment_date.isoformat() if outcome.commitment_date is not None else None
    )
    dialogue = compose_voice_reply(
        settings,
        utterance=speech or digits,
        intent=outcome.intent,
        commitment_date=commitment_date,
    )
    if outcome.intent == "PAY_ON_DATE":
        case.state = CaseState.WAITING.value
        state_note = f"Customer expects to pay by {commitment_date or 'a future date'}."
    elif outcome.intent == "PAY_TODAY":
        case.state = CaseState.OBSERVING.value
        state_note = "Customer indicated they expect to pay today; awaiting trusted Razorpay event."
    elif outcome.intent == "NEEDS_HELP":
        case.state = CaseState.HUMAN_REVIEW.value
        state_note = "Customer requested human help."
    elif outcome.intent == "DECLINES_PAYMENT":
        case.state = CaseState.STOPPED.value
        case.terminal_reason = "CUSTOMER_DECLINED_VOICE"
        state_note = "Customer declined payment; further automated contact stopped."
    else:
        case.state = CaseState.HUMAN_REVIEW.value
        state_note = "Voice response could not be interpreted safely; human review required."
    case.state_version += 1
    event_summary = state_note
    db.add(
        ConversationEvent(
            case_id=case.id,
            channel="voice",
            direction="INBOUND",
            content_redacted=speech or (f"Keypad response: {digits}" if digits else event_summary),
            extracted={
                "intent": outcome.intent,
                "commitment_date": commitment_date,
                "confidence": outcome.confidence,
                "input_mode": "dtmf" if digits else "speech" if speech else "none",
                "call_sid": call_sid or None,
                "reply_source": dialogue.source,
                "assistant_reply": dialogue.text,
            },
            extractor_version="voice-ivr-rules-v1",
        )
    )
    result = dict(action.result or {})
    result["voice_response"] = {
        "intent": outcome.intent,
        "commitment_date": commitment_date,
        "confidence": outcome.confidence,
        "call_sid": call_sid or None,
        "reply_source": dialogue.source,
    }
    action.result = result
    append_audit(
        db,
        merchant_id=case.merchant_id,
        aggregate_type="recovery_action",
        aggregate_id=action.id,
        event_kind="VOICE_RESPONSE_RECORDED",
        actor_type="customer",
        actor_id="twilio-voice-ivr",
        decision={
            "intent": outcome.intent,
            "commitment_date": commitment_date,
            "confidence": outcome.confidence,
        },
    )
    follow_up = None
    if outcome.intent in {"PAY_ON_DATE", "PAY_TODAY"}:
        follow_up = schedule_voice_payment_link(db, voice_action=action)
    db.commit()
    if follow_up is not None:
        background_tasks.add_task(execute_scheduled_case_action, follow_up.id, 0.5)
    audio_url = build_voice_audio_url(settings, text=dialogue.text)
    document = (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Play>'
        f"{escape(audio_url)}</Play></Response>"
    )
    return Response(content=document, media_type="application/xml")


@app.get("/api/v1/operations/health", response_model=OperationsHealth)
def operations_health(db: Session = Depends(get_db)) -> OperationsHealth:
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
        redis_status: Literal["ready", "unavailable"] = "ready"
    except Exception:
        redis_status = "unavailable"
    pending_outbox_events = (
        db.query(OutboxEvent).filter(OutboxEvent.dispatched_at.is_(None)).count()
    )
    failed_webhook_events = (
        db.query(WebhookEvent).filter(WebhookEvent.processing_error.is_not(None)).count()
    )
    return OperationsHealth(
        status="ready" if redis_status == "ready" else "degraded",
        database="ready",
        redis=redis_status,
        pending_outbox_events=pending_outbox_events,
        failed_webhook_events=failed_webhook_events,
    )


@app.post(
    "/api/v1/webhooks/razorpay",
    response_model=WebhookAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_razorpay_signature: str = Header(default=""),
    x_razorpay_event_id: str = Header(default=""),
) -> WebhookAccepted:
    raw_body = await request.body()
    try:
        result = ingest_razorpay_webhook(
            db,
            raw_body=raw_body,
            signature=x_razorpay_signature,
            provider_event_id=x_razorpay_event_id,
            current_secret=settings.razorpay_webhook_secret,
            previous_secret=settings.razorpay_previous_webhook_secret,
        )
    except WebhookRejected as exc:
        code = 401 if "signature" in str(exc).lower() else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return WebhookAccepted(duplicate=result.duplicate, event_id=result.event_id)


@app.get("/api/v1/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(db: Session = Depends(get_db)) -> DashboardSummary:
    merchant = active_merchant(db)
    cases = db.query(RecoveryCase).filter(RecoveryCase.merchant_id == merchant.id).all()
    actions = (
        db.query(RecoveryAction)
        .join(RecoveryRun, RecoveryRun.id == RecoveryAction.run_id)
        .filter(RecoveryRun.merchant_id == merchant.id)
        .all()
    )

    terminal = {
        CaseState.RECOVERED_VERIFIED.value,
        CaseState.STOPPED.value,
        CaseState.EXHAUSTED.value,
    }
    return DashboardSummary(
        revenue_at_risk_minor=sum(
            case.risk_amount_minor for case in cases if case.state not in terminal
        ),
        verified_recovered_minor=sum(case.recovered_amount_minor for case in cases),
        active_cases=sum(case.state not in terminal for case in cases),
        stopped_cases=sum(
            case.state in {CaseState.STOPPED.value, CaseState.EXHAUSTED.value} for case in cases
        ),
        escalated_cases=sum(case.state == CaseState.HUMAN_REVIEW.value for case in cases),
        customers_contacted=sum(action.contact_units for action in actions if action.executed_at),
        contacts_avoided=sum(
            action.action_type in {"WAIT", "NATIVE_RETRY_WAIT", "STOP"} for action in actions
        ),
        recovery_spend_minor=sum(action.cost_minor for action in actions if action.executed_at),
        action_counts=dict(Counter(action.action_type for action in actions)),
        state_counts=dict(Counter(case.state for case in cases)),
    )


@app.post("/api/v1/demo/reset", dependencies=[Depends(require_internal_token)])
def reset_demo_data() -> dict[str, object]:
    """Reset the four-case recovery portfolio and restore safe test contacts."""
    seed_demo(reset=True)
    configure_demo_contacts()
    return {
        "reset": True,
        "message": "Fresh four-case recovery portfolio and test contacts are ready.",
    }


@app.get(
    "/api/v1/razorpay/test-import/preview",
    response_model=list[RazorpayTestImportCandidate],
    dependencies=[Depends(require_internal_token)],
)
def preview_razorpay_test_import(
    count: int = Query(default=25, ge=1, le=100),
) -> list[RazorpayTestImportCandidate]:
    try:
        candidates = preview_failed_subscription_payments(settings, count=count)
    except Exception as exc:
        logger.warning("razorpay_test_import_preview_failed", error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [
        RazorpayTestImportCandidate(
            payment_id=item.payment_id,
            amount_minor=item.amount_minor,
            currency=item.currency,
            occurred_at=item.occurred_at,
            failure_code=item.failure_code,
            failure_reason=item.failure_reason,
            failure_description=item.failure_description,
            subscription_id=item.subscription_id,
            invoice_id=item.invoice_id,
            eligible=item.eligible,
            skip_reason=item.skip_reason,
        )
        for item in candidates
    ]


@app.post(
    "/api/v1/razorpay/test-import",
    response_model=RazorpayTestImportResult,
    dependencies=[Depends(require_internal_token)],
)
def import_razorpay_test_payments(
    body: RazorpayTestImportRequest, db: Session = Depends(get_db)
) -> RazorpayTestImportResult:
    try:
        imported_case_ids, skipped_payment_ids = import_selected_test_payments(
            db, settings, payment_ids=body.payment_ids
        )
    except Exception as exc:
        db.rollback()
        logger.warning("razorpay_test_import_failed", error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RazorpayTestImportResult(
        imported_case_ids=imported_case_ids, skipped_payment_ids=skipped_payment_ids
    )


@app.get("/api/v1/recovery-cases", response_model=list[RecoveryCaseListItem])
def list_cases(
    case_state: str | None = Query(default=None, alias="state"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[RecoveryCaseListItem]:
    merchant = active_merchant(db)
    query = (
        db.query(RecoveryCase, Customer, Subscription)
        .join(Customer, Customer.id == RecoveryCase.customer_id)
        .join(Subscription, Subscription.id == RecoveryCase.subscription_id)
        .filter(RecoveryCase.merchant_id == merchant.id)
    )
    if case_state:
        query = query.filter(RecoveryCase.state == case_state)
    rows = query.order_by(RecoveryCase.created_at.desc()).limit(limit).all()
    return [
        RecoveryCaseListItem(
            id=row.id,
            state=row.state,
            risk_amount_minor=row.risk_amount_minor,
            currency=row.currency,
            natural_recovery_score=row.natural_recovery_score,
            diagnosis=row.diagnosis,
            created_at=row.created_at,
            customer_name=customer.display_name,
            customer_segment=str(customer.contact_preferences.get("segment", "Subscription")),
            source_type=str(customer.contact_preferences.get("source_type", "Failed subscription")),
            provider_reference=subscription.razorpay_subscription_id,
            recommended_action=str(customer.contact_preferences.get("recommended_action", "WAIT")),
            next_step=str(
                customer.contact_preferences.get("next_step", "Observe provider outcome")
            ),
            recovery_priority=str(customer.contact_preferences.get("priority", "MEDIUM")),
        )
        for row, customer, subscription in rows
    ]


def _webhook_source(payload: dict[str, object]) -> str:
    if payload.get("_chaseless_source") == "razorpay_test_api_import":
        return "razorpay_test_api_import"
    event_payload = payload.get("payload")
    if not isinstance(event_payload, dict):
        return "provider"
    for entity_name in ("subscription", "payment", "payment_link"):
        wrapper = event_payload.get(entity_name)
        if not isinstance(wrapper, dict):
            continue
        entity = wrapper.get("entity")
        if not isinstance(entity, dict):
            continue
        notes = entity.get("notes")
        if isinstance(notes, dict) and notes.get("demo_source") == "synthetic_fixture":
            return "synthetic_fixture"
    return "provider"


@app.get("/api/v1/webhook-events", response_model=list[WebhookEventListItem])
def list_webhook_events(
    limit: int = Query(default=25, ge=1, le=100), db: Session = Depends(get_db)
) -> list[WebhookEventListItem]:
    merchant = active_merchant(db)
    rows = (
        db.query(WebhookEvent)
        .filter(WebhookEvent.merchant_id == merchant.id)
        .order_by(WebhookEvent.received_at.desc())
        .limit(limit)
        .all()
    )
    return [
        WebhookEventListItem(
            id=row.id,
            provider_event_id=row.provider_event_id,
            event_type=row.event_type,
            source=_webhook_source(row.payload),
            signature_valid=row.signature_valid,
            occurred_at=row.occurred_at,
            received_at=row.received_at,
            processed_at=row.processed_at,
            processing_error=row.processing_error,
        )
        for row in rows
    ]


@app.put(
    "/api/v1/customers/{customer_id}/contact-endpoints/whatsapp",
    response_model=CustomerContactResponse,
    dependencies=[Depends(require_internal_token)],
)
def set_whatsapp_endpoint(
    customer_id: uuid.UUID,
    body: WhatsAppEndpointRequest,
    db: Session = Depends(get_db),
) -> CustomerContactResponse:
    merchant = active_merchant(db)
    customer = (
        db.query(Customer)
        .filter(Customer.id == customer_id, Customer.merchant_id == merchant.id)
        .one_or_none()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    try:
        masked_endpoint = store_whatsapp_endpoint(customer, settings, body.e164, body.consent)
    except ContactEncryptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    append_audit(
        db,
        merchant_id=merchant.id,
        aggregate_type="customer",
        aggregate_id=customer.id,
        event_kind="CONTACT_ENDPOINT_UPDATED",
        actor_type="internal_user",
        actor_id="contact-endpoint-api",
        decision={
            "channel": "whatsapp",
            "consent": body.consent,
            "masked_endpoint": masked_endpoint,
        },
    )
    return CustomerContactResponse(
        customer_id=customer.id,
        channel="whatsapp",
        consent=body.consent,
        masked_endpoint=masked_endpoint,
    )


@app.put(
    "/api/v1/customers/{customer_id}/contact-endpoints/phone/{channel}",
    response_model=CustomerContactResponse,
    dependencies=[Depends(require_internal_token)],
)
def set_phone_endpoint(
    customer_id: uuid.UUID,
    channel: Literal["sms", "voice"],
    body: PhoneEndpointRequest,
    db: Session = Depends(get_db),
) -> CustomerContactResponse:
    merchant = active_merchant(db)
    customer = (
        db.query(Customer)
        .filter(Customer.id == customer_id, Customer.merchant_id == merchant.id)
        .one_or_none()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    try:
        masked_endpoint = store_phone_endpoint(
            customer, settings, channel=channel, e164=body.e164, consent=body.consent
        )
    except ContactEncryptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    append_audit(
        db,
        merchant_id=merchant.id,
        aggregate_type="customer",
        aggregate_id=customer.id,
        event_kind="CONTACT_ENDPOINT_UPDATED",
        actor_type="internal_user",
        actor_id="contact-endpoint-api",
        decision={"channel": channel, "consent": body.consent, "masked_endpoint": masked_endpoint},
    )
    return CustomerContactResponse(
        customer_id=customer.id,
        channel=channel,
        consent=body.consent,
        masked_endpoint=masked_endpoint,
    )


@app.put(
    "/api/v1/customers/{customer_id}/contact-endpoints/email",
    response_model=CustomerContactResponse,
    dependencies=[Depends(require_internal_token)],
)
def set_email_endpoint(
    customer_id: uuid.UUID,
    body: EmailEndpointRequest,
    db: Session = Depends(get_db),
) -> CustomerContactResponse:
    merchant = active_merchant(db)
    customer = (
        db.query(Customer)
        .filter(Customer.id == customer_id, Customer.merchant_id == merchant.id)
        .one_or_none()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    try:
        masked_endpoint = store_email_endpoint(customer, settings, body.email, body.consent)
    except ContactEncryptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    append_audit(
        db,
        merchant_id=merchant.id,
        aggregate_type="customer",
        aggregate_id=customer.id,
        event_kind="CONTACT_ENDPOINT_UPDATED",
        actor_type="internal_user",
        actor_id="contact-endpoint-api",
        decision={"channel": "email", "consent": body.consent, "masked_endpoint": masked_endpoint},
    )
    return CustomerContactResponse(
        customer_id=customer.id,
        channel="email",
        consent=body.consent,
        masked_endpoint=masked_endpoint,
    )


@app.get("/api/v1/recovery-cases/{case_id}", response_model=RecoveryCaseDetail)
def get_case(case_id: uuid.UUID, db: Session = Depends(get_db)) -> RecoveryCaseDetail:
    row = db.get(RecoveryCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")
    customer = db.get(Customer, row.customer_id)
    subscription = db.get(Subscription, row.subscription_id)
    if customer is None or subscription is None:
        raise HTTPException(status_code=409, detail="Recovery case context is incomplete")
    preferences = customer.contact_preferences or {}
    case_timeline = (
        db.query(AuditEvent)
        .filter(AuditEvent.aggregate_type == "recovery_case", AuditEvent.aggregate_id == case_id)
        .all()
    )

    action_timeline = (
        db.query(AuditEvent)
        .join(RecoveryAction, AuditEvent.aggregate_id == RecoveryAction.id)
        .filter(
            AuditEvent.aggregate_type == "recovery_action",
            RecoveryAction.case_id == case_id,
        )
        .all()
    )
    customer_timeline = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.aggregate_type == "customer",
            AuditEvent.aggregate_id == row.customer_id,
        )
        .all()
    )
    conversation_timeline = (
        db.query(ConversationEvent).filter(ConversationEvent.case_id == case_id).all()
    )
    timeline = [
        {
            "id": str(item.id),
            "kind": item.event_kind,
            "aggregate_type": item.aggregate_type,
            "actor": item.actor_id,
            "decision": item.decision,
            "created_at": item.created_at.isoformat(),
        }
        for item in [*case_timeline, *action_timeline, *customer_timeline]
    ]
    timeline.extend(
        {
            "id": str(item.id),
            "kind": f"{item.channel.upper()}_{item.direction}",
            "aggregate_type": "conversation",
            "actor": "delivery-provider",
            "decision": {"content_redacted": item.content_redacted, **item.extracted},
            "created_at": item.occurred_at.isoformat(),
        }
        for item in conversation_timeline
    )
    timeline.sort(key=lambda item: str(item["created_at"]))
    return RecoveryCaseDetail(
        id=row.id,
        state=row.state,
        risk_amount_minor=row.risk_amount_minor,
        currency=row.currency,
        natural_recovery_score=row.natural_recovery_score,
        diagnosis=row.diagnosis,
        created_at=row.created_at,
        customer_id=row.customer_id,
        subscription_id=row.subscription_id,
        episode_key=row.episode_key,
        contact_count=row.contact_count,
        replan_count=row.replan_count,
        terminal_reason=row.terminal_reason,
        recovered_amount_minor=row.recovered_amount_minor,
        recovered_at=row.recovered_at,
        timeline=timeline,
        customer_name=customer.display_name,
        customer_segment=str(preferences.get("segment", "Subscription")),
        source_type=str(preferences.get("source_type", "Failed subscription")),
        provider_reference=subscription.razorpay_subscription_id,
        recommended_action=str(preferences.get("recommended_action", "WAIT")),
        next_step=str(preferences.get("next_step", "Observe provider outcome")),
        recovery_priority=str(preferences.get("priority", "MEDIUM")),
        customer={
            "name": customer.display_name,
            "email": customer.email_masked,
            "phone": customer.phone_masked,
            "segment": preferences.get("segment", "Subscription"),
            "tenure_months": preferences.get("tenure_months", 8),
            "lifetime_value_minor": preferences.get(
                "lifetime_value_minor", row.risk_amount_minor * 8
            ),
            "contacts_7d": customer.contacts_7d,
            "promise_to_pay_at": customer.promise_to_pay_at.isoformat()
            if customer.promise_to_pay_at
            else None,
            "opted_out": customer.opted_out,
        },
        subscription={
            "id": subscription.razorpay_subscription_id,
            "plan_id": subscription.razorpay_plan_id,
            "status": subscription.status,
            "amount_minor": subscription.amount_minor,
            "next_charge_at": subscription.next_charge_at.isoformat()
            if subscription.next_charge_at
            else None,
        },
        payment_history=list(preferences.get("payment_history", [])),
    )


def _merchant_case(case_id: uuid.UUID, db: Session) -> RecoveryCase:
    merchant = active_merchant(db)
    case = (
        db.query(RecoveryCase)
        .filter(RecoveryCase.id == case_id, RecoveryCase.merchant_id == merchant.id)
        .one_or_none()
    )
    if case is None:
        raise HTTPException(status_code=404, detail="Recovery case not found")
    return case


def _case_automation_state(case: RecoveryCase, db: Session) -> CaseAutomationState:
    action = (
        db.query(RecoveryAction)
        .filter(RecoveryAction.case_id == case.id)
        .order_by(RecoveryAction.created_at.desc())
        .first()
    )
    if case.state == CaseState.RECOVERED_VERIFIED.value:
        return CaseAutomationState(
            case_id=case.id, status="RECOVERED", updated_at=case.recovered_at
        )
    if action is None:
        return CaseAutomationState(case_id=case.id, status="IDLE")
    result = dict(action.result or {})
    decision_value = result.get("llm_decision")
    decision = decision_value if isinstance(decision_value, dict) else {}
    voice_response_value = result.get("voice_response")
    if action.status == "SCHEDULED":
        state = "SCHEDULED"
    elif action.status == "EXECUTING":
        state = "EXECUTING"
    elif action.status == "FAILED":
        state = "FAILED"
    elif action.status in {"SKIPPED", "CANCELLED", "APPROVAL_REQUIRED"}:
        state = "BLOCKED"
    elif action.action_type == "VOICE_AGENT" and not result.get("voice_response"):
        state = "WAITING_FOR_RESPONSE"
    elif action.action_type == "PAYMENT_LINK" and result.get("short_url"):
        state = "WAITING_FOR_PAYMENT"
    elif action.action_type in {"NUDGE", "UPDATE_PAYMENT_METHOD"} and action.status == "SUCCEEDED":
        state = "WAITING_FOR_PAYMENT"
    elif action.action_type == "VOICE_AGENT" and result.get("voice_response"):
        state = "WAITING_FOR_PAYMENT"
    elif action.action_type == "HUMAN_ESCALATE" and action.status == "SUCCEEDED":
        state = "HUMAN_REVIEW"
    else:
        state = "BLOCKED" if action.status == "APPROVAL_REQUIRED" else "IDLE"
    return CaseAutomationState(
        case_id=case.id,
        status=state,
        action_id=action.id,
        action_type=action.action_type,
        action_status=action.status,
        rationale=decision.get("rationale"),
        decision_source=decision.get("source"),
        payment_url=result.get("short_url"),
        delivery_channel=result.get("delivery_channel") or result.get("channel"),
        delivery_provider=result.get("delivery_provider") or result.get("provider"),
        delivery_warning=result.get("delivery_warning"),
        voice_response=voice_response_value if isinstance(voice_response_value, dict) else None,
        error=action.last_error,
        updated_at=action.executed_at or action.scheduled_at or action.created_at,
    )


@app.get(
    "/api/v1/recovery-cases/{case_id}/automation",
    response_model=CaseAutomationState,
    dependencies=[Depends(require_internal_token)],
)
def get_case_automation(case_id: uuid.UUID, db: Session = Depends(get_db)) -> CaseAutomationState:
    return _case_automation_state(_merchant_case(case_id, db), db)


@app.post(
    "/api/v1/recovery-cases/{case_id}/automation/start",
    response_model=CaseAutomationState,
    dependencies=[Depends(require_internal_token)],
)
def start_automation(
    case_id: uuid.UUID,
    body: CaseAutomationStartRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> CaseAutomationState:
    del body  # the command id is accepted for a stable client command contract.
    case = _merchant_case(case_id, db)
    try:
        action = start_case_automation(db, case=case, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(execute_scheduled_case_action, action.id)
    return _case_automation_state(case, db)


@app.get(
    "/api/v1/recovery-cases/{case_id}/simulation",
    response_model=SimulationState,
    dependencies=[Depends(require_internal_token)],
)
def get_case_simulation(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> SimulationState:
    case = _merchant_case(case_id, db)
    result = latest_simulation(db, case)
    if result is None:
        raise HTTPException(status_code=404, detail="No simulation has been started")
    return SimulationState.model_validate(result)


@app.post(
    "/api/v1/recovery-cases/{case_id}/simulation/start",
    response_model=SimulationState,
    dependencies=[Depends(require_internal_token)],
)
def start_case_simulation(
    case_id: uuid.UUID,
    body: SimulationStartRequest,
    db: Session = Depends(get_db),
) -> SimulationState:
    case = _merchant_case(case_id, db)
    result = start_simulation(db, case, command_id=body.command_id)
    return SimulationState.model_validate(result)


@app.post(
    "/api/v1/recovery-cases/{case_id}/simulation/advance",
    response_model=SimulationState,
    dependencies=[Depends(require_internal_token)],
)
def advance_case_simulation(
    case_id: uuid.UUID,
    body: SimulationAdvanceRequest,
    db: Session = Depends(get_db),
) -> SimulationState:
    case = _merchant_case(case_id, db)
    try:
        result = advance_simulation(
            db,
            case,
            simulation_id=body.simulation_id,
            command_id=body.command_id,
            expected_progress=body.expected_progress,
            promise_to_pay=body.promise_to_pay,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SimulationState.model_validate(result)


@app.get("/api/v1/review-queue")
def review_queue(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    """List actions that require an explicit human decision or follow-up."""
    rows = (
        db.query(RecoveryAction, RecoveryCase)
        .join(RecoveryCase, RecoveryAction.case_id == RecoveryCase.id)
        .filter(
            or_(
                RecoveryAction.status == "APPROVAL_REQUIRED",
                RecoveryCase.state == CaseState.HUMAN_REVIEW.value,
            )
        )
        .order_by(RecoveryAction.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "action_id": str(action.id),
            "case_id": str(case.id),
            "action_type": action.action_type,
            "action_status": action.status,
            "case_state": case.state,
            "amount_minor": case.risk_amount_minor,
            "currency": case.currency,
            "requires_approval": action.requires_approval,
            "created_at": action.created_at.isoformat(),
        }
        for action, case in rows
    ]


@app.get("/api/v1/recovery-actions/recent")
def recent_recovery_actions(
    limit: int = Query(default=12, ge=1, le=50), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    merchant = active_merchant(db)
    rows = (
        db.query(RecoveryAction)
        .join(RecoveryRun, RecoveryRun.id == RecoveryAction.run_id)
        .filter(RecoveryRun.merchant_id == merchant.id)
        .order_by(RecoveryAction.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(action.id),
            "case_id": str(action.case_id),
            "action_type": action.action_type,
            "status": action.status,
            "channel": action.result.get("channel") or action.result.get("delivery_channel"),
            "provider": action.result.get("provider") or action.result.get("delivery_provider"),
            "payment_url": action.result.get("short_url"),
            "error": action.last_error,
            "created_at": action.created_at.isoformat(),
            "executed_at": action.executed_at.isoformat() if action.executed_at else None,
        }
        for action in rows
    ]


def _run_response(db: Session, run: RecoveryRun) -> RecoveryRunResponse:
    actions = (
        db.query(RecoveryAction)
        .filter(RecoveryAction.run_id == run.id)
        .order_by(RecoveryAction.created_at)
        .all()
    )
    return RecoveryRunResponse(
        id=run.id,
        status=run.status,
        budget_minor=run.budget_minor,
        contact_budget=run.contact_budget,
        reserved_cost_minor=run.reserved_cost_minor,
        reserved_contacts=run.reserved_contacts,
        estimated_incremental_minor=run.estimated_incremental_minor,
        plan_version=run.plan_version,
        created_at=run.created_at,
        approved_at=run.approved_at,
        executed_at=run.executed_at,
        actions=[
            {
                "id": str(action.id),
                "case_id": str(action.case_id),
                "type": action.action_type,
                "status": action.status,
                "requires_approval": action.requires_approval,
                "cost_minor": action.cost_minor,
            }
            for action in actions
        ],
    )


@app.post(
    "/api/v1/recovery-runs/preview",
    response_model=RecoveryRunResponse,
    dependencies=[Depends(require_internal_token)],
)
def preview_run(
    body: RecoveryRunPreviewRequest, db: Session = Depends(get_db)
) -> RecoveryRunResponse:
    merchant = active_merchant(db)
    run = preview_recovery_run(
        db,
        merchant=merchant,
        budget_minor=body.budget_minor,
        contact_budget=body.contact_budget,
        filters=body.filters,
    )
    return _run_response(db, run)


@app.get("/api/v1/recovery-runs/{run_id}", response_model=RecoveryRunResponse)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RecoveryRunResponse:
    run = db.get(RecoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Recovery run not found")
    return _run_response(db, run)


@app.post(
    "/api/v1/recovery-runs/{run_id}/approve",
    response_model=RecoveryRunResponse,
    dependencies=[Depends(require_internal_token)],
)
def approve_recovery_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RecoveryRunResponse:
    run = db.get(RecoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Recovery run not found")
    try:
        approve_run(db, run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_response(db, run)


@app.post(
    "/api/v1/recovery-runs/{run_id}/execute",
    response_model=RecoveryRunResponse,
    dependencies=[Depends(require_internal_token)],
)
def execute_recovery_run(
    run_id: uuid.UUID,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> RecoveryRunResponse:
    run = db.get(RecoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Recovery run not found")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    try:
        execute_run(db, run, idempotency_key=idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_response(db, run)


@app.post(
    "/api/v1/recovery-actions/{action_id}/approval",
    dependencies=[Depends(require_internal_token)],
)
def approve_recovery_action(
    action_id: uuid.UUID,
    body: ApprovalRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    action = db.get(RecoveryAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Recovery action not found")
    try:
        decide_action_approval(
            db,
            action,
            approve=body.approve,
            decided_by="demo-manager",
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": action.id, "status": action.status}


@app.get("/api/v1/evaluation/latest")
def latest_evaluation() -> dict[str, object]:
    result_path = Path(settings.evaluation_output_dir) / "results.json"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="No benchmark result has been generated")
    return cast(dict[str, object], json.loads(result_path.read_text(encoding="utf-8")))
