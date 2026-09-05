from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from chaseless.domain.enums import ActionType, PolicyVerdict
from chaseless.domain.types import ActionCandidate, RecoveryContext


class PolicyConfig(BaseModel):
    version: int = 1
    max_contacts_24h: int = 1
    max_contacts_7d: int = 3
    quiet_hours_start: time = time(21, 0)
    quiet_hours_end: time = time(9, 0)
    promise_to_pay_grace_hours: int = 12
    min_eirv_minor: int = 100
    max_interventions_per_case: int = 4
    require_approval: set[ActionType] = Field(
        default_factory=lambda: {
            ActionType.NUDGE,
            ActionType.UPDATE_PAYMENT_METHOD,
            ActionType.PAYMENT_LINK,
            ActionType.VOICE_AGENT,
        }
    )
    auto_allowed: set[ActionType] = Field(
        default_factory=lambda: {
            ActionType.WAIT,
            ActionType.NATIVE_RETRY_WAIT,
            ActionType.STOP,
            ActionType.HUMAN_ESCALATE,
        }
    )


def _is_quiet_hours(now: datetime, timezone_name: str, config: PolicyConfig) -> bool:
    local_time = now.astimezone(ZoneInfo(timezone_name)).time().replace(tzinfo=None)
    start, end = config.quiet_hours_start, config.quiet_hours_end
    if start > end:
        return local_time >= start or local_time < end
    return start <= local_time < end


def evaluate_candidate(
    candidate: ActionCandidate,
    context: RecoveryContext,
    config: PolicyConfig,
    *,
    now: datetime | None = None,
) -> ActionCandidate:
    now = now or datetime.now(UTC)
    reasons: list[str] = []
    action = candidate.action_type
    contact_action = candidate.contact_units > 0

    if action == ActionType.STOP:
        reason = "POOR_ECONOMICS" if candidate.eirv_minor <= 0 else "POLICY_STOP"
        return candidate.model_copy(
            update={
                "eligible": True,
                "policy_verdict": PolicyVerdict.ALLOW,
                "policy_reasons": [reason],
            }
        )

    if context.opted_out and contact_action:
        reasons.append("CUSTOMER_OPTED_OUT")
    if context.contacts_24h >= config.max_contacts_24h and contact_action:
        reasons.append("CONTACT_LIMIT_24H")
    if context.contacts_7d >= config.max_contacts_7d and contact_action:
        reasons.append("CONTACT_LIMIT_7D")
    if context.intervention_count >= config.max_interventions_per_case and action not in {
        ActionType.WAIT,
        ActionType.NATIVE_RETRY_WAIT,
    }:
        reasons.append("INTERVENTIONS_EXHAUSTED")
    if (
        context.promise_to_pay_at
        and contact_action
        and context.promise_to_pay_at.timestamp() + config.promise_to_pay_grace_hours * 3600
        > now.timestamp()
    ):
        reasons.append("PROMISE_TO_PAY_ACTIVE")
    if action == ActionType.PAYMENT_LINK and context.subscription_status.lower() != "halted":
        reasons.append("PAYMENT_LINK_REQUIRES_HALTED")
    if (
        action in {ActionType.NUDGE, ActionType.UPDATE_PAYMENT_METHOD}
        and "whatsapp" not in context.consent_channels
    ):
        reasons.append("WHATSAPP_CONSENT_REQUIRED")
    if action == ActionType.VOICE_AGENT and "voice" not in context.consent_channels:
        reasons.append("VOICE_CONSENT_REQUIRED")
    if candidate.eirv_minor < config.min_eirv_minor and action not in {
        ActionType.WAIT,
        ActionType.NATIVE_RETRY_WAIT,
    }:
        reasons.append("EIRV_BELOW_THRESHOLD")

    if reasons:
        verdict = (
            PolicyVerdict.DEFER if set(reasons) <= {"PROMISE_TO_PAY_ACTIVE"} else PolicyVerdict.DENY
        )
        return candidate.model_copy(
            update={
                "eligible": False,
                "policy_verdict": verdict,
                "policy_reasons": reasons,
            }
        )

    if contact_action and _is_quiet_hours(now, context.customer_timezone, config):
        return candidate.model_copy(
            update={
                "eligible": False,
                "policy_verdict": PolicyVerdict.DEFER,
                "policy_reasons": ["QUIET_HOURS"],
            }
        )

    requires_approval = action in config.require_approval
    verdict = PolicyVerdict.REQUIRE_APPROVAL if requires_approval else PolicyVerdict.ALLOW
    return candidate.model_copy(
        update={
            "eligible": True,
            "requires_approval": requires_approval,
            "policy_verdict": verdict,
            "policy_reasons": ["POLICY_ELIGIBLE"],
        }
    )


def apply_policy(
    candidates: list[ActionCandidate],
    context: RecoveryContext,
    config: PolicyConfig,
    *,
    now: datetime | None = None,
) -> list[ActionCandidate]:
    return [evaluate_candidate(item, context, config, now=now) for item in candidates]
