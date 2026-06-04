"""
biobrain.cognition — Pluggable reasoning specialists
======================================================

Each reasoner implements the Reasoner protocol and is registered
in the REGISTRY. The executive chooses which one; reason() dispatches.
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..core.enums import ReasoningMode
from ..core.signals import ExecutiveDecision, CognitiveResult, ModeState

logger = logging.getLogger("biobrain.cognition")


class Reasoner(Protocol):
    """Protocol for reasoning backends. Implement this to add new reasoners."""
    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult: ...


# ─── Built-in reasoners ──────────────────────────────────────────────────────

class DirectReasoner:
    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
        return CognitiveResult(
            decision=decision, reasoning_mode_used=ReasoningMode.DIRECT,
            confidence=0.7, reasoning_trace=["direct: pattern-matched response"],
        )


class ChecklistReasoner:
    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
        trace = ["checklist: systematic verification"]
        if decision.memory and decision.memory.procedural:
            trace.append(f"procedural_memories: {len(decision.memory.procedural)}")
        return CognitiveResult(
            decision=decision, reasoning_mode_used=ReasoningMode.CHECKLIST,
            confidence=0.8, reasoning_trace=trace,
        )


class CausalReasoner:
    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
        trace = ["causal: root cause analysis"]
        if decision.memory and decision.memory.episodic:
            trace.append(f"episodic_context: {len(decision.memory.episodic)} events")
        return CognitiveResult(
            decision=decision, reasoning_mode_used=ReasoningMode.CAUSAL,
            confidence=0.6, reasoning_trace=trace,
        )


class PlanningReasoner:
    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
        return CognitiveResult(
            decision=decision, reasoning_mode_used=ReasoningMode.PLANNING,
            confidence=0.65, reasoning_trace=["planning: decomposition"],
        )


class RetrievalReasoner:
    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
        trace = ["retrieval: grounded in evidence"]
        evidence = []
        if decision.memory:
            for label, items in [("semantic", decision.memory.semantic),
                                 ("episodic", decision.memory.episodic)]:
                for m in items:
                    evidence.append(f"[{label}] {m.text[:200]}")
                    trace.append(f"evidence_from_{label}")
        confidence = min(0.9, 0.4 + 0.1 * len(evidence))
        return CognitiveResult(
            decision=decision, reasoning_mode_used=ReasoningMode.RETRIEVAL,
            evidence=evidence, confidence=confidence, reasoning_trace=trace,
        )


class CriticReasoner:
    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
        trace = ["critic: verification and challenge"]
        if decision.salience.conflicts:
            trace.append(f"conflicts: {decision.salience.conflicts}")
        return CognitiveResult(
            decision=decision, reasoning_mode_used=ReasoningMode.CRITIC,
            confidence=0.55, reasoning_trace=trace,
        )


class SimulationReasoner:
    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
        return CognitiveResult(
            decision=decision, reasoning_mode_used=ReasoningMode.SIMULATION,
            confidence=0.5, reasoning_trace=["simulation: what-if analysis"],
        )


# ─── Registry ────────────────────────────────────────────────────────────────

REGISTRY: dict[ReasoningMode, Reasoner] = {
    ReasoningMode.DIRECT: DirectReasoner(),
    ReasoningMode.CHECKLIST: ChecklistReasoner(),
    ReasoningMode.CAUSAL: CausalReasoner(),
    ReasoningMode.PLANNING: PlanningReasoner(),
    ReasoningMode.RETRIEVAL: RetrievalReasoner(),
    ReasoningMode.CRITIC: CriticReasoner(),
    ReasoningMode.SIMULATION: SimulationReasoner(),
}


def register_reasoner(mode: ReasoningMode, reasoner: Reasoner) -> None:
    """Register or replace a reasoning backend."""
    REGISTRY[mode] = reasoner
    logger.info("Registered reasoner for %s: %s", mode.value, type(reasoner).__name__)


def reason(decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
    """Dispatch to the appropriate reasoning specialist."""
    reasoner = REGISTRY.get(decision.chosen_reasoning, REGISTRY[ReasoningMode.DIRECT])
    logger.info("Reasoning with: %s", decision.chosen_reasoning.value)
    return reasoner.run(decision, mode)
