from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    version: str


class DashboardSummary(BaseModel):
    revenue_at_risk_minor: int
    verified_recovered_minor: int
    active_cases: int
    stopped_cases: int
    escalated_cases: int
    customers_contacted: int
    contacts_avoided: int
    recovery_spend_minor: int
    action_counts: dict[str, int]
    state_counts: dict[str, int]


class RecoveryCaseListItem(BaseModel):
    id: uuid.UUID
    state: str
    risk_amount_minor: int
    currency: str
    natural_recovery_score: float
    diagnosis: dict[str, Any]
    created_at: datetime
    customer_name: str = "Customer"
    customer_segment: str = "Subscription"
    source_type: str = "Failed subscription"
    provider_reference: str = ""
    recommended_action: str = "WAIT"
    next_step: str = "Observe provider outcome"
    recovery_priority: str = "MEDIUM"


class RecoveryCaseDetail(RecoveryCaseListItem):
    customer_id: uuid.UUID
    subscription_id: uuid.UUID
    episode_key: str
    contact_count: int
    replan_count: int
    terminal_reason: str | None
    recovered_amount_minor: int
    recovered_at: datetime | None
    timeline: list[dict[str, Any]]
    customer: dict[str, Any] = Field(default_factory=dict)
    subscription: dict[str, Any] = Field(default_factory=dict)
    payment_history: list[dict[str, Any]] = Field(default_factory=list)


class RecoveryRunPreviewRequest(BaseModel):
    budget_minor: int = Field(gt=0)
    contact_budget: int = Field(ge=0)
    filters: dict[str, Any] = Field(default_factory=dict)


class RecoveryRunResponse(BaseModel):
    id: uuid.UUID
    status: str
    budget_minor: int
    contact_budget: int
    reserved_cost_minor: int
    reserved_contacts: int
    estimated_incremental_minor: int
    plan_version: int
    created_at: datetime
    approved_at: datetime | None
    executed_at: datetime | None
    actions: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    approve: bool
    reason: str = Field(min_length=3, max_length=1000)


class WebhookAccepted(BaseModel):
    accepted: bool = True
    duplicate: bool
    event_id: uuid.UUID | None = None


class WebhookEventListItem(BaseModel):
    id: uuid.UUID
    provider_event_id: str
    event_type: str
    source: str
    signature_valid: bool
    occurred_at: datetime | None
    received_at: datetime
    processed_at: datetime | None
    processing_error: str | None


class WhatsAppEndpointRequest(BaseModel):
    e164: str = Field(min_length=9, max_length=16)
    consent: bool


class PhoneEndpointRequest(BaseModel):
    e164: str = Field(min_length=9, max_length=16)
    consent: bool


class EmailEndpointRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    consent: bool


class CustomerContactResponse(BaseModel):
    customer_id: uuid.UUID
    channel: Literal["whatsapp", "sms", "voice", "email"]
    consent: bool
    masked_endpoint: str


class OperationsHealth(BaseModel):
    status: Literal["ready", "degraded"]
    database: Literal["ready"]
    redis: Literal["ready", "unavailable"]
    pending_outbox_events: int
    failed_webhook_events: int


class SimulationStartRequest(BaseModel):
    command_id: uuid.UUID


class SimulationAdvanceRequest(BaseModel):
    simulation_id: uuid.UUID
    command_id: uuid.UUID
    expected_progress: int = Field(ge=0, le=6)
    promise_to_pay: bool = False


class SimulationStep(BaseModel):
    progress: int
    event_kind: str
    label: str
    detail: str
    actor: str
    created_at: datetime


class SimulationState(BaseModel):
    simulation_id: uuid.UUID
    case_id: uuid.UUID
    progress: int
    status: Literal["RUNNING", "COMPLETED"]
    outcome: Literal["in_progress", "recovered", "stopped"]
    completed_steps: list[SimulationStep]
    promise_to_pay_at: datetime | None
    recovered_amount_minor: int
    payment_url: str | None
    last_event_at: datetime


class CaseAutomationStartRequest(BaseModel):
    command_id: uuid.UUID


class CaseAutomationState(BaseModel):
    case_id: uuid.UUID
    status: Literal[
        "IDLE",
        "SCHEDULED",
        "EXECUTING",
        "WAITING_FOR_RESPONSE",
        "WAITING_FOR_PAYMENT",
        "HUMAN_REVIEW",
        "RECOVERED",
        "FAILED",
        "BLOCKED",
    ]
    action_id: uuid.UUID | None = None
    action_type: str | None = None
    action_status: str | None = None
    rationale: str | None = None
    decision_source: str | None = None
    payment_url: str | None = None
    delivery_channel: str | None = None
    delivery_provider: str | None = None
    delivery_warning: str | None = None
    voice_response: dict[str, Any] | None = None
    error: str | None = None
    updated_at: datetime | None = None


class RazorpayTestImportCandidate(BaseModel):
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


class RazorpayTestImportRequest(BaseModel):
    payment_ids: list[str] = Field(min_length=1, max_length=25)


class RazorpayTestImportResult(BaseModel):
    imported_case_ids: list[uuid.UUID]
    skipped_payment_ids: list[str]
