from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from pydantic import BaseModel, ConfigDict

from chaseless.domain.allocation import allocate_budget
from chaseless.domain.diagnosis import diagnose
from chaseless.domain.enums import ActionType
from chaseless.domain.policy import PolicyConfig, apply_policy
from chaseless.domain.scoring import ECONOMICS, score_actions
from chaseless.domain.types import RecoveryContext

COHORTS = (
    "SELF_HEALER",
    "TIMING_SENSITIVE",
    "INSTRUMENT_BROKEN",
    "NUDGE_RESPONSIVE",
    "FATIGUED",
    "UNRECOVERABLE",
)


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    seed: int = 20260901
    customers: int = 10_000
    budget_minor: int = 1_000_000
    contact_budget: int = 3_500
    dataset_version: str = "synthetic-v1"


@dataclass(frozen=True)
class VisibleCustomer:
    customer_id: str
    cohort_label_for_evaluation: str
    amount_minor: int
    failure_code: str
    subscription_status: str
    prior_failures: int
    successful_payments: int
    contacts_7d: int
    opted_out: bool
    median_recovery_hours: float | None


@dataclass(frozen=True)
class HiddenWorld:
    natural_probability: float
    action_probabilities: dict[ActionType, float]
    outcome_uniform: float
    time_uniform: float


@dataclass(frozen=True)
class SyntheticCustomer:
    visible: VisibleCustomer
    hidden: HiddenWorld


@dataclass(frozen=True)
class StrategyDecision:
    action: ActionType
    policy_violation: bool = False


@dataclass(frozen=True)
class CaseOutcome:
    customer_id: str
    cohort: str
    strategy: str
    action: ActionType
    recovered: bool
    recovered_amount_minor: int
    contacts: int
    spend_minor: int
    time_to_recover_hours: float | None
    policy_violation: bool


def _profile(cohort: str) -> tuple[str, str, float, dict[ActionType, float]]:
    profiles = {
        "SELF_HEALER": (
            "UNKNOWN",
            "pending",
            0.78,
            {ActionType.NUDGE: 0.80, ActionType.UPDATE_PAYMENT_METHOD: 0.78},
        ),
        "TIMING_SENSITIVE": (
            "INSUFFICIENT_FUNDS",
            "pending",
            0.48,
            {ActionType.NUDGE: 0.66, ActionType.UPDATE_PAYMENT_METHOD: 0.5},
        ),
        "INSTRUMENT_BROKEN": (
            "CARD_EXPIRED",
            "halted",
            0.05,
            {
                ActionType.NUDGE: 0.09,
                ActionType.UPDATE_PAYMENT_METHOD: 0.74,
                ActionType.PAYMENT_LINK: 0.6,
            },
        ),
        "NUDGE_RESPONSIVE": (
            "UNKNOWN",
            "active",
            0.12,
            {ActionType.NUDGE: 0.66, ActionType.UPDATE_PAYMENT_METHOD: 0.2},
        ),
        "FATIGUED": (
            "UNKNOWN",
            "pending",
            0.05,
            {ActionType.NUDGE: 0.06, ActionType.UPDATE_PAYMENT_METHOD: 0.06},
        ),
        "UNRECOVERABLE": (
            "ACCOUNT_CLOSED",
            "halted",
            0.01,
            {ActionType.NUDGE: 0.01, ActionType.UPDATE_PAYMENT_METHOD: 0.01},
        ),
    }
    return profiles[cohort]


def generate_population(config: BenchmarkConfig) -> list[SyntheticCustomer]:
    rng = random.Random(config.seed)
    weights = [0.25, 0.18, 0.2, 0.2, 0.1, 0.07]
    population: list[SyntheticCustomer] = []
    for index in range(config.customers):
        cohort = rng.choices(COHORTS, weights=weights, k=1)[0]
        failure, status, natural, response = _profile(cohort)
        amount = rng.choice([49_900, 79_900, 99_900, 149_900, 249_900, 499_900])
        contacts = rng.randint(2, 4) if cohort == "FATIGUED" else rng.randint(0, 1)
        opted_out = cohort == "FATIGUED" and rng.random() < 0.25
        successful = rng.randint(8, 24) if cohort == "SELF_HEALER" else rng.randint(1, 10)
        prior_failures = 0 if cohort == "SELF_HEALER" else rng.randint(1, 5)
        recovery_hours = rng.uniform(4, 24) if cohort == "SELF_HEALER" else rng.uniform(36, 96)
        all_probabilities = {action: natural for action in ActionType}
        all_probabilities.update(response)
        all_probabilities[ActionType.VOICE_AGENT] = max(
            all_probabilities.get(ActionType.VOICE_AGENT, natural), min(natural + 0.2, 0.85)
        )
        all_probabilities[ActionType.HUMAN_ESCALATE] = max(
            all_probabilities.get(ActionType.HUMAN_ESCALATE, natural), min(natural + 0.12, 0.8)
        )
        population.append(
            SyntheticCustomer(
                visible=VisibleCustomer(
                    customer_id=f"syn-{index:05d}",
                    cohort_label_for_evaluation=cohort,
                    amount_minor=amount,
                    failure_code=failure,
                    subscription_status=status,
                    prior_failures=prior_failures,
                    successful_payments=successful,
                    contacts_7d=contacts,
                    opted_out=opted_out,
                    median_recovery_hours=recovery_hours,
                ),
                hidden=HiddenWorld(
                    natural_probability=natural,
                    action_probabilities=all_probabilities,
                    outcome_uniform=rng.random(),
                    time_uniform=rng.random(),
                ),
            )
        )
    return population


def _context(customer: VisibleCustomer) -> RecoveryContext:
    return RecoveryContext(
        case_id=customer.customer_id,
        amount_minor=customer.amount_minor,
        subscription_status=customer.subscription_status,
        failure_code=customer.failure_code,
        prior_failures=customer.prior_failures,
        successful_payments=customer.successful_payments,
        contacts_7d=customer.contacts_7d,
        opted_out=customer.opted_out,
        median_recovery_hours=customer.median_recovery_hours,
    )


def _chaseless_decisions(
    population: list[SyntheticCustomer], config: BenchmarkConfig
) -> dict[str, StrategyDecision]:
    policy = PolicyConfig(require_approval=set())
    candidates = []
    evaluation_time = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    for customer in population:
        context = _context(customer.visible)
        result = diagnose(context)
        candidates.extend(
            apply_policy(score_actions(context, result), context, policy, now=evaluation_time)
        )
    allocation = allocate_budget(
        candidates,
        budget_minor=config.budget_minor,
        contact_budget=config.contact_budget,
    )
    return {
        item.candidate.case_id: StrategyDecision(action=item.candidate.action_type)
        for item in allocation.selected
    }


def _fixed_dunning_decisions(
    population: list[SyntheticCustomer], config: BenchmarkConfig
) -> dict[str, StrategyDecision]:
    remaining_budget = config.budget_minor
    remaining_contacts = config.contact_budget
    decisions: dict[str, StrategyDecision] = {}
    for customer in population:
        visible = customer.visible
        if visible.opted_out or visible.contacts_7d >= 3:
            decisions[visible.customer_id] = StrategyDecision(ActionType.STOP)
            continue
        cost = ECONOMICS[ActionType.NUDGE].cost_minor
        if remaining_budget >= cost and remaining_contacts > 0:
            decisions[visible.customer_id] = StrategyDecision(ActionType.NUDGE)
            remaining_budget -= cost
            remaining_contacts -= 1
        else:
            decisions[visible.customer_id] = StrategyDecision(ActionType.WAIT)
    return decisions


def _simulate(
    population: list[SyntheticCustomer], strategy: str, decisions: dict[str, StrategyDecision]
) -> list[CaseOutcome]:
    outcomes: list[CaseOutcome] = []
    for customer in population:
        decision = decisions.get(customer.visible.customer_id, StrategyDecision(ActionType.WAIT))
        action = decision.action
        if action in {ActionType.WAIT, ActionType.NATIVE_RETRY_WAIT, ActionType.STOP}:
            probability = customer.hidden.natural_probability
        else:
            probability = customer.hidden.action_probabilities.get(
                action, customer.hidden.natural_probability
            )
        recovered = customer.hidden.outcome_uniform < probability
        economics = ECONOMICS[action]
        outcomes.append(
            CaseOutcome(
                customer_id=customer.visible.customer_id,
                cohort=customer.visible.cohort_label_for_evaluation,
                strategy=strategy,
                action=action,
                recovered=recovered,
                recovered_amount_minor=customer.visible.amount_minor if recovered else 0,
                contacts=economics.contact_units,
                spend_minor=economics.cost_minor,
                time_to_recover_hours=(
                    round(2 + customer.hidden.time_uniform * 70, 2) if recovered else None
                ),
                policy_violation=decision.policy_violation,
            )
        )
    return outcomes


def _summary(outcomes: list[CaseOutcome]) -> dict[str, object]:
    recovery_times = [
        item.time_to_recover_hours for item in outcomes if item.time_to_recover_hours is not None
    ]
    ordered = sorted(recovery_times)
    p90_index = min(round(len(ordered) * 0.9), len(ordered) - 1) if ordered else 0
    return {
        "recovered_minor": sum(item.recovered_amount_minor for item in outcomes),
        "recovered_cases": sum(item.recovered for item in outcomes),
        "contacts": sum(item.contacts for item in outcomes),
        "spend_minor": sum(item.spend_minor for item in outcomes),
        "policy_violations": sum(item.policy_violation for item in outcomes),
        "median_time_to_recover_hours": round(median(recovery_times), 2)
        if recovery_times
        else None,
        "p90_time_to_recover_hours": ordered[p90_index] if ordered else None,
        "action_counts": dict(Counter(item.action.value for item in outcomes)),
    }


def run_benchmark(config: BenchmarkConfig) -> tuple[dict[str, object], list[CaseOutcome]]:
    population = generate_population(config)
    decisions: dict[str, dict[str, StrategyDecision]] = {
        "native_recovery": {
            item.visible.customer_id: StrategyDecision(ActionType.WAIT) for item in population
        },
        "fixed_dunning": _fixed_dunning_decisions(population, config),
        "chaseless": _chaseless_decisions(population, config),
    }
    all_outcomes: list[CaseOutcome] = []
    metrics: dict[str, object] = {}
    for strategy, strategy_decisions in decisions.items():
        outcomes = _simulate(population, strategy, strategy_decisions)
        all_outcomes.extend(outcomes)
        metrics[strategy] = _summary(outcomes)

    chase = metrics["chaseless"]
    fixed = metrics["fixed_dunning"]
    native = metrics["native_recovery"]
    assert isinstance(chase, dict) and isinstance(fixed, dict) and isinstance(native, dict)
    metrics["incremental"] = {
        "vs_fixed_dunning_minor": int(chase["recovered_minor"]) - int(fixed["recovered_minor"]),
        "vs_native_recovery_minor": int(chase["recovered_minor"]) - int(native["recovered_minor"]),
        "contacts_avoided_vs_fixed": int(fixed["contacts"]) - int(chase["contacts"]),
    }
    return metrics, all_outcomes
