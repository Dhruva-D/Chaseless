from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from chaseless.core.settings import Settings, get_settings
from chaseless.db.models import CandidateAction, Customer, RecoveryAction, RecoveryCase, RecoveryRun
from chaseless.db.session import session_scope
from chaseless.domain.enums import ActionStatus, CaseState, PolicyVerdict
from chaseless.domain.types import Diagnosis
from chaseless.integrations.llm.decision_advisor import choose_recovery_action
from chaseless.orchestration import evaluate_case
from chaseless.services.actions import execute_action
from chaseless.services.audit import append_audit
from chaseless.services.recovery import _active_policy, _context


def start_case_automation(db: Session, *, case: RecoveryCase, settings: Settings) -> RecoveryAction:
    """Create one bounded, policy-authorized action and schedule its real execution."""
    if case.state in {
        CaseState.RECOVERED_VERIFIED.value,
        CaseState.STOPPED.value,
        CaseState.EXHAUSTED.value,
    }:
        raise ValueError("This case is already in a terminal state")
    active = (
        db.query(RecoveryAction)
        .filter(
            RecoveryAction.case_id == case.id,
            RecoveryAction.status.in_([ActionStatus.SCHEDULED.value, ActionStatus.EXECUTING.value]),
        )
        .first()
    )
    if active is not None:
        return active
    policy_row, policy = _active_policy(db, case.merchant_id)
    graph = evaluate_case(_context(db, case), policy, Diagnosis.model_validate(case.diagnosis))
    candidates = graph["candidates"]
    eligible = [
        candidate
        for candidate in candidates
        if candidate.eligible and candidate.policy_verdict == PolicyVerdict.ALLOW
    ]
    if not eligible:
        raise ValueError("Policy did not authorize an automatic action for this case")
    eligible.sort(key=lambda item: item.eirv_minor, reverse=True)
    customer = db.get(Customer, case.customer_id)
    preferences = dict(customer.contact_preferences or {}) if customer is not None else {}
    preferred_action = str(preferences.get("recommended_action", "")).upper()
    # The portfolio lane is an operator constraint, not a hard-coded diagnosis rule. Gemini still
    # writes the decision and rationale, but the configured lane is presented first for fallback.
    eligible.sort(
        key=lambda item: (item.action_type.value == preferred_action, item.eirv_minor),
        reverse=True,
    )
    run = RecoveryRun(
        merchant_id=case.merchant_id,
        policy_version_id=policy_row.id,
        filters={"case_id": str(case.id), "trigger": "case_automation"},
        budget_minor=max(candidate.action_cost_minor for candidate in eligible),
        contact_budget=max(candidate.contact_units for candidate in eligible),
        status="EXECUTING",
        executed_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    rows: dict[str, CandidateAction] = {}
    for candidate in candidates:
        row = CandidateAction(
            run_id=run.id,
            case_id=case.id,
            action_type=candidate.action_type.value,
            probability_action=candidate.probability_action,
            probability_natural=candidate.probability_natural,
            action_cost_minor=candidate.action_cost_minor,
            fatigue_penalty_minor=candidate.fatigue_penalty_minor,
            risk_penalty_minor=candidate.risk_penalty_minor,
            eirv_minor=candidate.eirv_minor,
            contact_units=candidate.contact_units,
            eligible=candidate.eligible,
            policy_verdict=candidate.policy_verdict.value,
            policy_reasons=candidate.policy_reasons,
        )
        db.add(row)
        rows[candidate.action_type.value] = row
    db.flush()
    decision = choose_recovery_action(
        settings,
        context={
            "failure_reason": case.diagnosis.get("failure_class", "UNKNOWN"),
            "amount_minor": case.risk_amount_minor,
            "natural_recovery_score": case.natural_recovery_score,
            "contact_count": case.contact_count,
            "customer_segment": preferences.get("segment"),
            "source_type": preferences.get("source_type"),
            "successful_payment_count": len(
                [row for row in preferences.get("payment_history", []) if row.get("status") == "paid"]
            ),
            "merchant_recovery_lane": preferred_action or None,
            "eligible_actions": [candidate.action_type.value for candidate in eligible],
        },
        allowed_actions=[candidate.action_type.value for candidate in eligible],
    )
    selected = rows[decision.action_type]
    selected.selected, selected.rank = True, 1
    action = RecoveryAction(
        run_id=run.id,
        case_id=case.id,
        candidate_action_id=selected.id,
        action_type=decision.action_type,
        status=ActionStatus.SCHEDULED.value,
        scheduled_at=datetime.now(UTC),
        idempotency_key=f"case-automation:{case.id}:{uuid.uuid4()}",
        requires_approval=False,
        cost_minor=selected.action_cost_minor,
        contact_units=selected.contact_units,
        result={
            "llm_decision": {
                "action_type": decision.action_type,
                "rationale": decision.rationale,
                "source": decision.source,
            }
        },
    )
    db.add(action)
    # The audit event references the action aggregate, so allocate its UUID before appending it.
    db.flush()
    case.state, case.state_version = CaseState.ACTION_SCHEDULED.value, case.state_version + 1
    append_audit(
        db,
        merchant_id=case.merchant_id,
        aggregate_type="recovery_action",
        aggregate_id=action.id,
        event_kind="AI_DECISION_EXPLAINED",
        actor_type="ai",
        actor_id=decision.source,
        policy_version=policy.version,
        decision={
            "action_type": decision.action_type,
            "rationale": decision.rationale,
            "source": decision.source,
            "eligible_actions": [candidate.action_type.value for candidate in eligible],
        },
    )
    db.commit()
    return action


def schedule_voice_payment_link(db: Session, *, voice_action: RecoveryAction) -> RecoveryAction | None:
    """Schedule one payment-link follow-up after a captured promise to pay."""
    existing = (
        db.query(RecoveryAction)
        .filter(
            RecoveryAction.run_id == voice_action.run_id,
            RecoveryAction.action_type == "PAYMENT_LINK",
        )
        .first()
    )
    if existing is not None:
        return existing
    candidate = (
        db.query(CandidateAction)
        .filter(
            CandidateAction.run_id == voice_action.run_id,
            CandidateAction.action_type == "PAYMENT_LINK",
            CandidateAction.eligible.is_(True),
        )
        .first()
    )
    if candidate is None or candidate.policy_verdict != PolicyVerdict.ALLOW.value:
        return None
    voice_result = dict(voice_action.result or {})
    response = voice_result.get("voice_response")
    follow_up = RecoveryAction(
        run_id=voice_action.run_id,
        case_id=voice_action.case_id,
        candidate_action_id=candidate.id,
        action_type="PAYMENT_LINK",
        status=ActionStatus.SCHEDULED.value,
        scheduled_at=datetime.now(UTC),
        idempotency_key=f"voice-promise-payment-link:{voice_action.id}",
        requires_approval=False,
        cost_minor=candidate.action_cost_minor,
        contact_units=candidate.contact_units,
        result={
            "voice_response": response,
            "llm_decision": {
                "action_type": "PAYMENT_LINK",
                "rationale": "The customer committed to pay, so a secure Razorpay link is being sent to complete that promise.",
                "source": "voice-promise-follow-up",
            },
        },
    )
    db.add(follow_up)
    db.flush()
    return follow_up


def execute_scheduled_case_action(action_id: uuid.UUID, delay_seconds: float = 2.5) -> None:
    """Run after the API has acknowledged the click so the UI can show the decision first."""
    time.sleep(delay_seconds)
    with session_scope() as db:
        action = db.get(RecoveryAction, action_id)
        if action is not None:
            try:
                execute_action(db, action.id, get_settings())
            except Exception as exc:
                action.status = ActionStatus.FAILED.value
                action.last_error = (
                    f"AUTOMATION_EXECUTION_FAILED: {type(exc).__name__}"
                    + (f": {exc}" if str(exc) else "")
                )[:1000]
