from chaseless.domain.allocation import allocate_budget
from chaseless.domain.enums import ActionType, PolicyVerdict
from chaseless.domain.types import ActionCandidate


def option(case: str, action: ActionType, eirv: int, cost: int, contacts: int) -> ActionCandidate:
    return ActionCandidate(
        case_id=case,
        action_type=action,
        probability_action=0.5,
        probability_natural=0.2,
        action_cost_minor=cost,
        eirv_minor=eirv,
        contact_units=contacts,
        eligible=True,
        policy_verdict=PolicyVerdict.ALLOW,
    )


def test_allocator_obeys_spend_and_contact_budgets() -> None:
    candidates = [
        option("a", ActionType.NUDGE, 10_000, 250, 1),
        option("a", ActionType.WAIT, 0, 0, 0),
        option("b", ActionType.UPDATE_PAYMENT_METHOD, 20_000, 350, 1),
        option("b", ActionType.WAIT, 0, 0, 0),
    ]
    result = allocate_budget(candidates, budget_minor=500, contact_budget=1)
    assert result.reserved_contacts == 1
    assert result.reserved_cost_minor <= 500
    assert len(result.selected) == 2
    assert {item.candidate.case_id for item in result.selected} == {"a", "b"}
