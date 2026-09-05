from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from chaseless.db.base import Base
from chaseless.domain.enums import ActionStatus, CaseState


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Merchant(Base, TimestampMixin):
    __tablename__ = "merchants"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    status: Mapped[str] = mapped_column(String(32), default="active")


class MerchantPolicyVersion(Base):
    __tablename__ = "merchant_policy_versions"
    __table_args__ = (UniqueConstraint("merchant_id", "version"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    rules_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(200), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("merchant_id", "provider_customer_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    provider_customer_id: Mapped[str | None] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(200), default="Customer")
    email_masked: Mapped[str | None] = mapped_column(String(200))
    email_encrypted: Mapped[str | None] = mapped_column(Text)
    phone_masked: Mapped[str | None] = mapped_column(String(50))
    phone_e164_encrypted: Mapped[str | None] = mapped_column(Text)
    whatsapp_e164_encrypted: Mapped[str | None] = mapped_column(Text)
    contact_preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    contacts_24h: Mapped[int] = mapped_column(Integer, default=0)
    contacts_7d: Mapped[int] = mapped_column(Integer, default=0)
    promise_to_pay_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("merchant_id", "razorpay_subscription_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    razorpay_subscription_id: Mapped[str] = mapped_column(String(100))
    razorpay_plan_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    next_charge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("merchant_id", "provider_event_id"),
        Index("ix_webhook_unprocessed", "processed_at", "received_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="razorpay")
    provider_event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    signature_valid: Mapped[bool] = mapped_column(Boolean)
    raw_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_error: Mapped[str | None] = mapped_column(Text)


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (UniqueConstraint("merchant_id", "provider_payment_id", "event_kind"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    webhook_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_events.id"))
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id"), index=True
    )
    provider_payment_id: Mapped[str | None] = mapped_column(String(100))
    provider_invoice_id: Mapped[str | None] = mapped_column(String(100))
    event_kind: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40))
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    failure_code: Mapped[str | None] = mapped_column(String(100))
    failure_description: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecoveryCase(Base, TimestampMixin):
    __tablename__ = "recovery_cases"
    __table_args__ = (
        UniqueConstraint("merchant_id", "episode_key"),
        Index("ix_case_state_created", "state", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    subscription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    episode_key: Mapped[str] = mapped_column(String(240))
    state: Mapped[str] = mapped_column(String(40), default=CaseState.AT_RISK.value)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    risk_amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    diagnosis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    natural_recovery_score: Mapped[float] = mapped_column(Float, default=0.0)
    contact_count: Mapped[int] = mapped_column(Integer, default=0)
    replan_count: Mapped[int] = mapped_column(Integer, default=0)
    terminal_reason: Mapped[str | None] = mapped_column(String(100))
    recovered_amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryRun(Base, TimestampMixin):
    __tablename__ = "recovery_runs"
    __table_args__ = (UniqueConstraint("execute_idempotency_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchant_policy_versions.id"))
    status: Mapped[str] = mapped_column(String(40), default="PREVIEW")
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    budget_minor: Mapped[int] = mapped_column(BigInteger)
    contact_budget: Mapped[int] = mapped_column(Integer)
    reserved_cost_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_contacts: Mapped[int] = mapped_column(Integer, default=0)
    estimated_incremental_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execute_idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


class CandidateAction(Base, TimestampMixin):
    __tablename__ = "candidate_actions"
    __table_args__ = (UniqueConstraint("run_id", "case_id", "action_type"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_runs.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(60))
    probability_action: Mapped[float] = mapped_column(Float)
    probability_natural: Mapped[float] = mapped_column(Float)
    action_cost_minor: Mapped[int] = mapped_column(BigInteger)
    fatigue_penalty_minor: Mapped[int] = mapped_column(BigInteger)
    risk_penalty_minor: Mapped[int] = mapped_column(BigInteger)
    eirv_minor: Mapped[int] = mapped_column(BigInteger)
    contact_units: Mapped[int] = mapped_column(Integer, default=0)
    eligible: Mapped[bool] = mapped_column(Boolean)
    policy_verdict: Mapped[str] = mapped_column(String(40))
    policy_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    rank: Mapped[int | None] = mapped_column(Integer)


class RecoveryAction(Base, TimestampMixin):
    __tablename__ = "recovery_actions"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("provider_reference"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_runs.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), index=True)
    candidate_action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_actions.id"))
    action_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40), default=ActionStatus.PROPOSED.value)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    provider_reference: Mapped[str | None] = mapped_column(String(100))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    contact_units: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_actions.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text)


class ConversationEvent(Base):
    __tablename__ = "conversation_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id"), index=True)
    channel: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(20))
    content_redacted: Mapped[str] = mapped_column(Text)
    extracted: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extractor_version: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_aggregate", "aggregate_type", "aggregate_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(60))
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    event_kind: Mapped[str] = mapped_column(String(100))
    actor_type: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(200))
    decision: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy_version: Mapped[int | None] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_pending", "dispatched_at", "available_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(String(100))
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    seed: Mapped[int] = mapped_column(Integer)
    customer_count: Mapped[int] = mapped_column(Integer)
    dataset_version: Mapped[str] = mapped_column(String(100))
    config_hash: Mapped[str] = mapped_column(String(64))
    code_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_paths: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationCaseResult(Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (UniqueConstraint("evaluation_run_id", "synthetic_customer_id", "strategy"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id"), index=True
    )
    synthetic_customer_id: Mapped[str] = mapped_column(String(100))
    strategy: Mapped[str] = mapped_column(String(40))
    cohort: Mapped[str] = mapped_column(String(60))
    action_type: Mapped[str] = mapped_column(String(60))
    recovered: Mapped[bool] = mapped_column(Boolean)
    recovered_amount_minor: Mapped[int] = mapped_column(BigInteger)
    contacts: Mapped[int] = mapped_column(Integer)
    spend_minor: Mapped[int] = mapped_column(BigInteger)
    time_to_recover_hours: Mapped[float | None] = mapped_column(Float)
    policy_violation: Mapped[bool] = mapped_column(Boolean, default=False)


# Alembic imports this module for metadata discovery.
__all__ = [
    name
    for name, value in list(globals().items())
    if isinstance(value, type) and issubclass(value, Base)
]
