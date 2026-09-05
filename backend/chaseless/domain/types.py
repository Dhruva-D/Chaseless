from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from chaseless.domain.enums import ActionType, PolicyVerdict


class RecoveryContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    amount_minor: int = Field(gt=0)
    currency: str = "INR"
    subscription_status: str
    failure_code: str | None = None
    prior_failures: int = 0
    successful_payments: int = 0
    median_recovery_hours: float | None = None
    contacts_24h: int = 0
    contacts_7d: int = 0
    opted_out: bool = False
    promise_to_pay_at: datetime | None = None
    next_native_retry_at: datetime | None = None
    customer_timezone: str = "Asia/Kolkata"
    intervention_count: int = 0
    consent_channels: set[str] = Field(default_factory=set)


class Diagnosis(BaseModel):
    failure_class: str
    confidence: float = Field(ge=0, le=1)
    natural_recovery_score: float = Field(ge=0, le=1)
    evidence: list[str]
    source: str = "rules-v1"


class ActionCandidate(BaseModel):
    case_id: str
    action_type: ActionType
    probability_action: float = Field(ge=0, le=1)
    probability_natural: float = Field(ge=0, le=1)
    action_cost_minor: int = 0
    fatigue_penalty_minor: int = 0
    risk_penalty_minor: int = 0
    eirv_minor: int = 0
    contact_units: int = 0
    policy_verdict: PolicyVerdict = PolicyVerdict.DENY
    policy_reasons: list[str] = Field(default_factory=list)
    eligible: bool = False
    requires_approval: bool = False


class AllocatedAction(BaseModel):
    candidate: ActionCandidate
    rank: int


class AllocationResult(BaseModel):
    selected: list[AllocatedAction]
    rejected: list[ActionCandidate]
    reserved_cost_minor: int
    reserved_contacts: int
    estimated_incremental_minor: int
