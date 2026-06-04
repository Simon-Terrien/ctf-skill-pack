"""
biobrain.executive — Strategy selection, inhibition, action planning
=====================================================================

The prefrontal cortex. Chooses HOW to process, not WHAT to think.
Now uses structured policy checks instead of substring matching.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..core.enums import (
    ReasoningMode, ActionType, SystemMode, OperationClass,
)
from ..core.signals import (
    SalienceScore, MemoryResult, ExecutiveDecision, ModeState, IdentityState,
)
from ..identity import check_policy, needs_evidence

logger = logging.getLogger("biobrain.executive")


def decide(
    salience: SalienceScore,
    memory: Optional[MemoryResult] = None,
    mode: Optional[ModeState] = None,
    identity: Optional[IdentityState] = None,
) -> ExecutiveDecision:
    """Make an executive decision about how to process this signal."""
    mode = mode or ModeState()
    identity = identity or IdentityState()

    inhibited: list[str] = []
    policy_notes: list[str] = []
    chosen_actions: list[ActionType] = []

    # ── 1. Structured policy check (replaces substring matching) ──────────
    operation = salience.perceived.operation_class
    domain = salience.perceived.classification

    effect, reason = check_policy(identity, operation, domain, mode)

    if effect == "deny":
        inhibited.append(f"policy_deny:{operation.value}:{domain}")
        policy_notes.append(f"DENIED: {reason}")
        logger.info("INHIBITED by policy: %s", reason)
    elif effect == "require_approval":
        inhibited.append(f"needs_approval:{operation.value}")
        policy_notes.append(f"APPROVAL REQUIRED: {reason}")
        chosen_actions.append(ActionType.ESCALATION)

    # ── 2. Evidence requirement check ─────────────────────────────────────
    if needs_evidence(identity, domain):
        policy_notes.append(f"Evidence required for domain '{domain}'")

    # ── 3. Choose reasoning mode ─────────────────────────────────────────
    reasoning = _choose_reasoning(salience, memory, mode)

    # ── 4. Determine actions (only if not denied) ────────────────────────
    if effect != "deny":
        chosen_actions.extend(_determine_actions(salience, reasoning, mode))

    # ── 5. Mode-specific policy ──────────────────────────────────────────
    if mode.mode == SystemMode.AUDIT:
        policy_notes.append("AUDIT: require evidence and citations")
        reasoning = ReasoningMode.RETRIEVAL

    if mode.mode == SystemMode.INCIDENT:
        policy_notes.append("INCIDENT: prioritize speed and containment")

    if mode.mode == SystemMode.RISK:
        policy_notes.append("RISK: elevated caution")

    # ── 6. Confidence-based inhibition ───────────────────────────────────
    if salience.confidence < mode.confidence_floor:
        inhibited.append(f"low_confidence:{salience.confidence}")
        policy_notes.append(
            f"Confidence {salience.confidence} below floor {mode.confidence_floor}"
        )
        if ActionType.TOOL_CALL in chosen_actions:
            chosen_actions.remove(ActionType.TOOL_CALL)
            if ActionType.ESCALATION not in chosen_actions:
                chosen_actions.append(ActionType.ESCALATION)

    # ── 7. Risk-based inhibition ─────────────────────────────────────────
    if salience.risk_score > mode.autonomy_ceiling:
        inhibited.append(f"risk_exceeds_autonomy:{salience.risk_score}")
        policy_notes.append(
            f"Risk {salience.risk_score} exceeds autonomy ceiling {mode.autonomy_ceiling}"
        )
        chosen_actions = [ActionType.ESCALATION]

    if not chosen_actions:
        chosen_actions = [ActionType.NO_ACTION]

    return ExecutiveDecision(
        salience=salience, memory=memory,
        chosen_reasoning=reasoning, chosen_actions=chosen_actions,
        inhibited_actions=inhibited, policy_notes=policy_notes,
    )


def _choose_reasoning(
    salience: SalienceScore, memory: Optional[MemoryResult], mode: ModeState,
) -> ReasoningMode:
    suggested = salience.suggested_reasoning
    if memory and memory.procedural:
        return ReasoningMode.CHECKLIST
    if memory and len(memory.episodic) >= 3 and suggested == ReasoningMode.DIRECT:
        return ReasoningMode.RETRIEVAL
    if salience.conflicts:
        return ReasoningMode.CRITIC
    return suggested


def _determine_actions(
    salience: SalienceScore, reasoning: ReasoningMode, mode: ModeState,
) -> list[ActionType]:
    actions: list[ActionType] = []
    intent = salience.perceived.intent
    if intent in ("security_assessment", "configuration", "deployment", "creation", "remediation"):
        actions.append(ActionType.TOOL_CALL)
    elif intent in ("reporting", "explanation", "information_retrieval", "comparison", "planning"):
        actions.append(ActionType.REPORT)
    elif intent == "deletion":
        actions.append(ActionType.TOOL_CALL)
    if not actions:
        actions.append(ActionType.REPORT)
    return actions
