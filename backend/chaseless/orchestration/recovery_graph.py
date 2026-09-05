from __future__ import annotations

from typing import TypedDict, cast

from langgraph.graph import END, START, StateGraph

from chaseless.domain.diagnosis import diagnose
from chaseless.domain.policy import PolicyConfig, apply_policy
from chaseless.domain.scoring import score_actions
from chaseless.domain.types import ActionCandidate, Diagnosis, RecoveryContext


class RecoveryGraphState(TypedDict, total=False):
    context: RecoveryContext
    policy: PolicyConfig
    diagnosis: Diagnosis
    candidates: list[ActionCandidate]
    proposed: ActionCandidate | None
    rationale: str


def diagnosis_node(state: RecoveryGraphState) -> RecoveryGraphState:
    if "diagnosis" in state:
        return {}
    return {"diagnosis": diagnose(state["context"])}


def candidate_node(state: RecoveryGraphState) -> RecoveryGraphState:
    return {"candidates": score_actions(state["context"], state["diagnosis"])}


def policy_node(state: RecoveryGraphState) -> RecoveryGraphState:
    return {"candidates": apply_policy(state["candidates"], state["context"], state["policy"])}


def proposal_node(state: RecoveryGraphState) -> RecoveryGraphState:
    eligible = [candidate for candidate in state["candidates"] if candidate.eligible]
    proposed = max(eligible, key=lambda item: item.eirv_minor) if eligible else None
    if proposed is None:
        rationale = "No action passed deterministic policy."
    else:
        rationale = (
            f"{proposed.action_type.value} has EIRV {proposed.eirv_minor} minor units; "
            f"policy verdict {proposed.policy_verdict.value}."
        )
    return {"proposed": proposed, "rationale": rationale}


builder = StateGraph(RecoveryGraphState)
builder.add_node("diagnose", diagnosis_node)
builder.add_node("generate_candidates", candidate_node)
builder.add_node("policy_gate", policy_node)
builder.add_node("propose", proposal_node)
builder.add_edge(START, "diagnose")
builder.add_edge("diagnose", "generate_candidates")
builder.add_edge("generate_candidates", "policy_gate")
builder.add_edge("policy_gate", "propose")
builder.add_edge("propose", END)
recovery_graph = builder.compile()


def evaluate_case(
    context: RecoveryContext,
    policy: PolicyConfig,
    diagnosis: Diagnosis | None = None,
) -> RecoveryGraphState:
    initial: RecoveryGraphState = {"context": context, "policy": policy}
    if diagnosis is not None:
        initial["diagnosis"] = diagnosis
    return cast(
        RecoveryGraphState,
        recovery_graph.invoke(initial),
    )
