import uuid

import pytest
from chaseless.core.settings import Settings
from chaseless.db.models import (
    AuditEvent,
    ConversationEvent,
    Customer,
    Merchant,
    RecoveryCase,
    Subscription,
    WebhookEvent,
)
from chaseless.domain.enums import CaseState
from chaseless.services.demo_simulation import advance_simulation, start_simulation
from sqlalchemy.orm import Session


def recovery_case(db: Session, *, action: str = "NUDGE") -> tuple[RecoveryCase, Customer]:
    merchant = Merchant(name="Simulation Merchant")
    db.add(merchant)
    db.flush()
    customer = Customer(
        merchant_id=merchant.id,
        provider_customer_id="cust_simulation",
        display_name="Simulation Customer",
        contact_preferences={"recommended_action": action},
    )
    db.add(customer)
    db.flush()
    subscription = Subscription(
        merchant_id=merchant.id,
        customer_id=customer.id,
        razorpay_subscription_id="sub_simulation",
        razorpay_plan_id="plan_simulation",
        status="pending",
        amount_minor=125_000,
        currency="INR",
    )
    db.add(subscription)
    db.flush()
    case = RecoveryCase(
        merchant_id=merchant.id,
        customer_id=customer.id,
        subscription_id=subscription.id,
        episode_key="sub_simulation:cycle:test",
        state=CaseState.DIAGNOSED.value,
        risk_amount_minor=125_000,
        currency="INR",
        diagnosis={"failure_class": "TEMPORARY_LIQUIDITY"},
    )
    db.add(case)
    db.commit()
    return case, customer


def test_simulation_is_idempotent_and_recovers_from_signed_webhook(db: Session) -> None:
    case, customer = recovery_case(db)
    command_id = uuid.uuid4()
    started = start_simulation(db, case, command_id=command_id)
    duplicate = start_simulation(db, case, command_id=command_id)

    assert started["simulation_id"] == duplicate["simulation_id"]
    assert started["progress"] == 1
    assert (
        db.query(AuditEvent).filter(AuditEvent.event_kind == "DEMO_SIMULATION_STARTED").count() == 1
    )

    simulation_id = started["simulation_id"]
    state = started
    settings = Settings(app_env="test", razorpay_webhook_secret="simulation-secret")
    while state["progress"] < 7:
        expected = state["progress"]
        state = advance_simulation(
            db,
            case,
            simulation_id=simulation_id,
            command_id=uuid.uuid4(),
            expected_progress=expected,
            promise_to_pay=expected == 4,
            settings=settings,
        )

    db.refresh(case)
    db.refresh(customer)
    assert state["outcome"] == "recovered"
    assert case.state == CaseState.RECOVERED_VERIFIED.value
    assert case.recovered_amount_minor == case.risk_amount_minor
    assert customer.promise_to_pay_at is None
    webhook = db.query(WebhookEvent).one()
    assert webhook.signature_valid
    assert webhook.event_type == "subscription.charged"
    assert (
        webhook.payload["payload"]["subscription"]["entity"]["notes"]["demo_source"]
        == "synthetic_fixture"
    )


def test_simulation_rejects_a_stale_step(db: Session) -> None:
    case, _ = recovery_case(db)
    state = start_simulation(db, case, command_id=uuid.uuid4())

    with pytest.raises(ValueError, match="moved from step 0 to 1"):
        advance_simulation(
            db,
            case,
            simulation_id=state["simulation_id"],
            command_id=uuid.uuid4(),
            expected_progress=0,
            promise_to_pay=False,
            settings=Settings(app_env="test", razorpay_webhook_secret="simulation-secret"),
        )


def test_payment_link_step_returns_clickable_demo_checkout(db: Session) -> None:
    case, _ = recovery_case(db, action="PAYMENT_LINK")
    state = start_simulation(db, case, command_id=uuid.uuid4())
    for _ in range(3):
        state = advance_simulation(
            db,
            case,
            simulation_id=state["simulation_id"],
            command_id=uuid.uuid4(),
            expected_progress=state["progress"],
            promise_to_pay=False,
            settings=Settings(app_env="test", razorpay_webhook_secret="simulation-secret"),
        )

    assert state["progress"] == 4
    assert state["payment_url"] == (f"/pay/{case.id}?simulation_id={state['simulation_id']}")


def test_voice_promise_creates_redacted_conversation_evidence(db: Session) -> None:
    case, _ = recovery_case(db, action="VOICE_AGENT")
    state = start_simulation(db, case, command_id=uuid.uuid4())
    for _ in range(4):
        state = advance_simulation(
            db,
            case,
            simulation_id=state["simulation_id"],
            command_id=uuid.uuid4(),
            expected_progress=state["progress"],
            promise_to_pay=state["progress"] == 4,
            settings=Settings(app_env="test", razorpay_webhook_secret="simulation-secret"),
        )

    turns = db.query(ConversationEvent).order_by(ConversationEvent.occurred_at).all()
    assert [turn.direction for turn in turns] == ["OUTBOUND", "INBOUND"]
    assert turns[-1].extracted["intent"] == "PROMISE_TO_PAY"
    assert state["promise_to_pay_at"] is not None
