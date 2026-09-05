from dataclasses import dataclass

from chaseless.domain.enums import ActionType
from chaseless.domain.types import ActionCandidate, Diagnosis, RecoveryContext


@dataclass(frozen=True)
class ActionEconomics:
    cost_minor: int
    contact_units: int
    uplift: float
    base_risk_penalty_minor: int = 0


ECONOMICS: dict[ActionType, ActionEconomics] = {
    ActionType.WAIT: ActionEconomics(0, 0, 0.0),
    ActionType.NATIVE_RETRY_WAIT: ActionEconomics(0, 0, 0.0),
    ActionType.NUDGE: ActionEconomics(250, 1, 0.12),
    ActionType.UPDATE_PAYMENT_METHOD: ActionEconomics(350, 1, 0.28),
    ActionType.PAYMENT_LINK: ActionEconomics(500, 1, 0.22, 100),
    ActionType.VOICE_AGENT: ActionEconomics(3_000, 1, 0.32, 500),
    ActionType.HUMAN_ESCALATE: ActionEconomics(5_000, 0, 0.18, 700),
    ActionType.STOP: ActionEconomics(0, 0, 0.0),
}


def applicable_actions(context: RecoveryContext, diagnosis: Diagnosis) -> list[ActionType]:
    if diagnosis.failure_class == "NON_RECOVERABLE":
        return [ActionType.STOP]

    actions = [ActionType.WAIT, ActionType.STOP]
    status = context.subscription_status.lower()
    if status == "pending":
        actions.append(ActionType.NATIVE_RETRY_WAIT)
    actions.append(ActionType.NUDGE)

    if diagnosis.failure_class == "INSTRUMENT_ISSUE":
        actions.append(ActionType.UPDATE_PAYMENT_METHOD)
    if status == "halted":
        actions.extend([ActionType.UPDATE_PAYMENT_METHOD, ActionType.PAYMENT_LINK])
    if context.amount_minor >= 100_000:
        actions.append(ActionType.HUMAN_ESCALATE)
        actions.append(ActionType.VOICE_AGENT)
    return list(dict.fromkeys(actions))


def action_uplift(action: ActionType, diagnosis: Diagnosis, context: RecoveryContext) -> float:
    uplift = ECONOMICS[action].uplift
    if action == ActionType.NUDGE and diagnosis.failure_class == "TEMPORARY_LIQUIDITY":
        uplift = 0.09
    if action == ActionType.UPDATE_PAYMENT_METHOD and diagnosis.failure_class == "INSTRUMENT_ISSUE":
        uplift = 0.38
    if action == ActionType.PAYMENT_LINK and context.subscription_status.lower() == "halted":
        uplift = 0.34
    if action == ActionType.HUMAN_ESCALATE and context.amount_minor >= 500_000:
        uplift = 0.25
    return uplift


def score_actions(context: RecoveryContext, diagnosis: Diagnosis) -> list[ActionCandidate]:
    natural = diagnosis.natural_recovery_score
    candidates: list[ActionCandidate] = []
    fatigue_rate = min(0.04 * context.contacts_7d, 0.3)

    for action in applicable_actions(context, diagnosis):
        economics = ECONOMICS[action]
        if action in {ActionType.WAIT, ActionType.NATIVE_RETRY_WAIT}:
            probability_action = natural
        elif action == ActionType.STOP:
            probability_action = 0.0
        else:
            probability_action = min(natural + action_uplift(action, diagnosis, context), 0.97)

        fatigue_penalty = round(context.amount_minor * fatigue_rate * economics.contact_units)
        eirv = round(
            context.amount_minor * (probability_action - natural)
            - economics.cost_minor
            - fatigue_penalty
            - economics.base_risk_penalty_minor
        )
        candidates.append(
            ActionCandidate(
                case_id=context.case_id,
                action_type=action,
                probability_action=probability_action,
                probability_natural=natural,
                action_cost_minor=economics.cost_minor,
                fatigue_penalty_minor=fatigue_penalty,
                risk_penalty_minor=economics.base_risk_penalty_minor,
                eirv_minor=eirv,
                contact_units=economics.contact_units,
            )
        )
    return candidates
