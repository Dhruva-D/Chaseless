from datetime import UTC, datetime

from chaseless.domain.enums import ActionType, PolicyVerdict
from chaseless.domain.policy import PolicyConfig, evaluate_candidate
from chaseless.domain.types import ActionCandidate, RecoveryContext


def candidate(action: ActionType = ActionType.NUDGE) -> ActionCandidate:
    return ActionCandidate(
        case_id="case-1",
        action_type=action,
        probability_action=0.5,
        probability_natural=0.2,
        eirv_minor=10_000,
        contact_units=0 if action in {ActionType.WAIT, ActionType.STOP} else 1,
    )


def context(**updates: object) -> RecoveryContext:
    values: dict[str, object] = {
        "case_id": "case-1",
        "amount_minor": 100_000,
        "subscription_status": "pending",
    }
    values.update(updates)
    return RecoveryContext.model_validate(values)


def test_opt_out_blocks_contact() -> None:
    result = evaluate_candidate(
        candidate(),
        context(opted_out=True),
        PolicyConfig(),
        now=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    assert result.policy_verdict == PolicyVerdict.DENY
    assert "CUSTOMER_OPTED_OUT" in result.policy_reasons


def test_payment_link_requires_halted_subscription() -> None:
    result = evaluate_candidate(
        candidate(ActionType.PAYMENT_LINK),
        context(subscription_status="pending"),
        PolicyConfig(),
        now=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    assert not result.eligible
    assert "PAYMENT_LINK_REQUIRES_HALTED" in result.policy_reasons


def test_whatsapp_actions_require_explicit_consent() -> None:
    result = evaluate_candidate(
        candidate(ActionType.NUDGE),
        context(),
        PolicyConfig(),
        now=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    assert result.eligible is False
    assert "WHATSAPP_CONSENT_REQUIRED" in result.policy_reasons


def test_stop_is_always_auditable_policy_action() -> None:
    result = evaluate_candidate(
        candidate(ActionType.STOP).model_copy(update={"eirv_minor": -1}),
        context(opted_out=True),
        PolicyConfig(),
        now=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )
    assert result.eligible
    assert result.policy_verdict == PolicyVerdict.ALLOW
    assert result.policy_reasons == ["POOR_ECONOMICS"]
