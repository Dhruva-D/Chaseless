from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from chaseless.db.models import (
    Approval,
    CandidateAction,
    Customer,
    Merchant,
    MerchantPolicyVersion,
    OutboxEvent,
    RecoveryAction,
    RecoveryCase,
    RecoveryRun,
    Subscription,
)
from chaseless.domain.allocation import allocate_budget
from chaseless.domain.enums import ActionStatus, CaseState, PolicyVerdict
from chaseless.domain.policy import PolicyConfig
from chaseless.domain.types import Diagnosis, RecoveryContext
from chaseless.orchestration import evaluate_case
from chaseless.services.audit import append_audit


def _active_policy(
    db: Session, merchant_id: uuid.UUID
) -> tuple[MerchantPolicyVersion, PolicyConfig]:
    row = (
        db.query(MerchantPolicyVersion)
        .filter(MerchantPolicyVersion.merchant_id == merchant_id)
        .order_by(MerchantPolicyVersion.version.desc())
        .first()
    )
    if row is None:
        config = PolicyConfig()
        row = MerchantPolicyVersion(
            merchant_id=merchant_id,
            version=config.version,
            rules_json=config.model_dump(mode="json"),
        )
        db.add(row)
        db.flush()
    return row, PolicyConfig.model_validate(row.rules_json)


def _context(db: Session, case: RecoveryCase) -> RecoveryContext:
    customer = db.get(Customer, case.customer_id)
    subscription = db.get(Subscription, case.subscription_id)
    if customer is None or subscription is None:
        raise RuntimeError("Recovery case context is incomplete")
    diagnosis = case.diagnosis or {}
    evidence = diagnosis.get("evidence", [])
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
        customer_timezone="Asia/Kolkata",
        intervention_count=case.contact_count,
        consent_channels={key for key, value in customer.consent.items() if value},
    )


def preview_recovery_run(
    db: Session,
    *,
    merchant: Merchant,
    budget_minor: int,
    contact_budget: int,
    filters: dict[str, Any] | None = None,
) -> RecoveryRun:
    policy_row, policy = _active_policy(db, merchant.id)
    run = RecoveryRun(
        merchant_id=merchant.id,
        policy_version_id=policy_row.id,
        filters=filters or {},
        budget_minor=budget_minor,
        contact_budget=contact_budget,
    )
    db.add(run)
    db.flush()
    cases = (
        db.query(RecoveryCase)
        .filter(
            RecoveryCase.merchant_id == merchant.id,
            RecoveryCase.state.in_(
                [CaseState.AT_RISK.value, CaseState.DIAGNOSED.value, CaseState.REPLAN.value]
            ),
        )
        .all()
    )
    all_candidates = []
    db_rows: dict[tuple[str, str], CandidateAction] = {}
    for case in cases:
        context = _context(db, case)
        diagnosis = Diagnosis.model_validate(case.diagnosis)
        graph_result = evaluate_case(context, policy, diagnosis)
        candidates = graph_result["candidates"]
        all_candidates.extend(candidates)
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
            db_rows[(candidate.case_id, candidate.action_type.value)] = row
    db.flush()
    allocation = allocate_budget(
        all_candidates, budget_minor=budget_minor, contact_budget=contact_budget
    )
    run.reserved_cost_minor = allocation.reserved_cost_minor
    run.reserved_contacts = allocation.reserved_contacts
    run.estimated_incremental_minor = allocation.estimated_incremental_minor
    for selected in allocation.selected:
        candidate = selected.candidate
        candidate_row = db_rows[(candidate.case_id, candidate.action_type.value)]
        candidate_row.selected = True
        candidate_row.rank = selected.rank
        status = (
            ActionStatus.APPROVAL_REQUIRED.value
            if candidate.policy_verdict == PolicyVerdict.REQUIRE_APPROVAL
            else ActionStatus.PROPOSED.value
        )
        action = RecoveryAction(
            run_id=run.id,
            case_id=uuid.UUID(candidate.case_id),
            candidate_action_id=candidate_row.id,
            action_type=candidate.action_type.value,
            status=status,
            idempotency_key=f"run:{run.id}:case:{candidate.case_id}:{candidate.action_type.value}",
            requires_approval=candidate.requires_approval,
            cost_minor=candidate.action_cost_minor,
            contact_units=candidate.contact_units,
        )
        db.add(action)
        selected_case = db.get(RecoveryCase, uuid.UUID(candidate.case_id))
        if selected_case:
            selected_case.state = CaseState.PLANNED.value
            selected_case.state_version += 1
    append_audit(
        db,
        merchant_id=merchant.id,
        aggregate_type="recovery_run",
        aggregate_id=run.id,
        event_kind="RECOVERY_PLAN_CREATED",
        actor_type="system",
        actor_id="budget-autopilot-v1",
        policy_version=policy.version,
        decision={"output": allocation.model_dump(mode="json")},
    )
    db.commit()
    return run


def approve_run(db: Session, run: RecoveryRun) -> RecoveryRun:
    if run.status != "PREVIEW":
        raise ValueError("Only preview runs can be approved")
    run.status = "APPROVED"
    run.approved_at = datetime.now(UTC)
    db.commit()
    return run


def execute_run(
    db: Session,
    run: RecoveryRun,
    *,
    idempotency_key: str,
) -> RecoveryRun:
    if not idempotency_key or len(idempotency_key) > 200:
        raise ValueError("A valid execution idempotency key is required")
    existing = (
        db.query(RecoveryRun)
        .filter(RecoveryRun.execute_idempotency_key == idempotency_key)
        .one_or_none()
    )
    if existing is not None and existing.id != run.id:
        raise ValueError("Idempotency-Key was already used for another recovery run")
    if run.execute_idempotency_key is not None:
        if run.execute_idempotency_key != idempotency_key:
            raise ValueError("Recovery run was already executed with another Idempotency-Key")
        return run
    if run.status == "EXECUTING":
        return run
    if run.status != "APPROVED":
        raise ValueError("Recovery run must be approved before execution")
    run.execute_idempotency_key = idempotency_key
    run.status = "EXECUTING"
    run.executed_at = datetime.now(UTC)
    actions = db.query(RecoveryAction).filter(RecoveryAction.run_id == run.id).all()
    for action in actions:
        if action.requires_approval:
            continue
        action.status = ActionStatus.SCHEDULED.value
        db.add(
            OutboxEvent(
                topic="recovery.action.execute",
                aggregate_id=action.id,
                payload={"action_id": str(action.id)},
            )
        )
    if any(action.requires_approval for action in actions):
        run.status = "AWAITING_APPROVAL"
    db.commit()
    return run


def decide_action_approval(
    db: Session,
    action: RecoveryAction,
    *,
    approve: bool,
    decided_by: str,
    reason: str,
) -> RecoveryAction:
    if action.status != ActionStatus.APPROVAL_REQUIRED.value:
        raise ValueError("Action is not awaiting approval")
    db.add(
        Approval(
            action_id=action.id,
            status="APPROVED" if approve else "REJECTED",
            decided_at=datetime.now(UTC),
            decided_by=decided_by,
            reason=reason,
        )
    )
    if approve:
        action.status = ActionStatus.SCHEDULED.value
        db.add(
            OutboxEvent(
                topic="recovery.action.execute",
                aggregate_id=action.id,
                payload={"action_id": str(action.id)},
            )
        )
    else:
        action.status = ActionStatus.CANCELLED.value
    db.commit()
    return action
