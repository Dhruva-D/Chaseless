from collections import defaultdict

from chaseless.domain.enums import ActionType
from chaseless.domain.types import ActionCandidate, AllocatedAction, AllocationResult

PASSIVE_ACTIONS = {ActionType.WAIT, ActionType.NATIVE_RETRY_WAIT, ActionType.STOP}


def _value_density(candidate: ActionCandidate) -> tuple[float, int, str]:
    constrained_units = candidate.action_cost_minor + candidate.contact_units * 100
    density = candidate.eirv_minor / max(constrained_units, 1)
    return (-density, -candidate.eirv_minor, candidate.case_id)


def allocate_budget(
    candidates: list[ActionCandidate], *, budget_minor: int, contact_budget: int
) -> AllocationResult:
    by_case: dict[str, list[ActionCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_case[candidate.case_id].append(candidate)

    active: list[ActionCandidate] = []
    passive_fallback: dict[str, ActionCandidate] = {}
    rejected: list[ActionCandidate] = []

    for case_id, options in by_case.items():
        eligible = [option for option in options if option.eligible]
        active_options = [
            option
            for option in eligible
            if option.action_type not in PASSIVE_ACTIONS and option.eirv_minor > 0
        ]
        if active_options:
            best = max(
                active_options, key=lambda option: (option.eirv_minor, option.action_type.value)
            )
            active.append(best)
            rejected.extend(option for option in options if option is not best)
            continue

        passive = [option for option in eligible if option.action_type in PASSIVE_ACTIONS]
        if passive:
            priority = {
                ActionType.NATIVE_RETRY_WAIT: 3,
                ActionType.WAIT: 2,
                ActionType.STOP: 1,
            }
            best_passive = max(passive, key=lambda option: priority[option.action_type])
            passive_fallback[case_id] = best_passive
            rejected.extend(option for option in options if option is not best_passive)
        else:
            rejected.extend(options)

    selected_candidates: list[ActionCandidate] = []
    cost = 0
    contacts = 0
    for candidate in sorted(active, key=_value_density):
        if (
            cost + candidate.action_cost_minor <= budget_minor
            and contacts + candidate.contact_units <= contact_budget
        ):
            selected_candidates.append(candidate)
            cost += candidate.action_cost_minor
            contacts += candidate.contact_units
        else:
            rejected.append(candidate)
            fallback = next(
                (
                    item
                    for item in by_case[candidate.case_id]
                    if item.eligible and item.action_type in PASSIVE_ACTIONS
                ),
                None,
            )
            if fallback:
                passive_fallback[candidate.case_id] = fallback

    selected_case_ids = {candidate.case_id for candidate in selected_candidates}
    selected_candidates.extend(
        fallback
        for case_id, fallback in passive_fallback.items()
        if case_id not in selected_case_ids
    )
    ranked = [
        AllocatedAction(candidate=candidate, rank=index)
        for index, candidate in enumerate(selected_candidates, start=1)
    ]
    return AllocationResult(
        selected=ranked,
        rejected=rejected,
        reserved_cost_minor=cost,
        reserved_contacts=contacts,
        estimated_incremental_minor=sum(max(item.candidate.eirv_minor, 0) for item in ranked),
    )
