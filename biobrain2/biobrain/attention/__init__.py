"""
biobrain.attention — Salience scoring, priority routing, reasoning suggestion
"""

from __future__ import annotations

import logging
from typing import Optional

from ..core.enums import (
    Priority, ReasoningMode, TrustLevel, SystemMode,
)
from ..core.signals import PerceivedInput, SalienceScore, ModeState

logger = logging.getLogger("biobrain.attention")

RISK_WEIGHTS: dict[str, float] = {
    "destructive_command": 0.9, "prompt_injection": 0.95,
    "sensitive_data": 0.7, "privilege_context": 0.6,
    "urgency_marker": 0.3, "security_context": 0.4,
    "untrusted_source": 0.5,
}

INTENT_TO_REASONING: dict[str, ReasoningMode] = {
    "security_assessment": ReasoningMode.CHECKLIST,
    "information_retrieval": ReasoningMode.RETRIEVAL,
    "creation": ReasoningMode.PLANNING,
    "remediation": ReasoningMode.CAUSAL,
    "explanation": ReasoningMode.DIRECT,
    "comparison": ReasoningMode.CRITIC,
    "planning": ReasoningMode.PLANNING,
    "reporting": ReasoningMode.RETRIEVAL,
    "deployment": ReasoningMode.CHECKLIST,
    "configuration": ReasoningMode.CHECKLIST,
    "deletion": ReasoningMode.CHECKLIST,
}


def score_salience(
    perceived: PerceivedInput, mode: Optional[ModeState] = None,
) -> SalienceScore:
    mode = mode or ModeState()
    risk = _compute_risk(perceived, mode)
    priority = _compute_priority(perceived, risk, mode)
    confidence = _compute_confidence(perceived)
    reasoning = _suggest_reasoning(perceived, risk, confidence, mode)
    conflicts = []
    if "prompt_injection" in perceived.risk_indicators:
        conflicts.append("potential_prompt_injection_detected")

    return SalienceScore(
        perceived=perceived, priority=priority, risk_score=risk,
        confidence=confidence, novelty=0.5, conflicts=conflicts,
        suggested_reasoning=reasoning,
    )


def _compute_risk(perceived: PerceivedInput, mode: ModeState) -> float:
    if not perceived.risk_indicators:
        base = 0.0
    else:
        weights = [RISK_WEIGHTS.get(r, 0.3) for r in perceived.risk_indicators]
        base = 0.7 * max(weights) + 0.3 * (sum(weights) / len(weights))
    if mode.mode == SystemMode.RISK:
        base = min(1.0, base * 1.3)
    elif mode.mode == SystemMode.INCIDENT:
        base = min(1.0, base * 1.2)
    if perceived.raw.trust == TrustLevel.UNTRUSTED:
        base = max(base, 0.3)
    elif perceived.raw.trust == TrustLevel.ADVERSARIAL:
        base = max(base, 0.7)
    return round(min(1.0, base), 3)


def _compute_priority(perceived: PerceivedInput, risk: float, mode: ModeState) -> Priority:
    if risk >= 0.8:
        return Priority.CRITICAL
    if risk >= 0.5:
        return Priority.HIGH
    if "urgency_marker" in perceived.risk_indicators:
        return Priority.HIGH
    if mode.mode == SystemMode.INCIDENT:
        return Priority.HIGH
    return Priority.NORMAL


def _compute_confidence(perceived: PerceivedInput) -> float:
    c = 0.5
    if perceived.intent != "general":
        c += 0.2
    if perceived.classification != "general":
        c += 0.15
    if perceived.entities:
        c += 0.1
    if perceived.risk_indicators:
        c += 0.05
    return round(min(1.0, c), 3)


def _suggest_reasoning(
    perceived: PerceivedInput, risk: float, confidence: float, mode: ModeState,
) -> ReasoningMode:
    if risk >= 0.7:
        return ReasoningMode.CHECKLIST
    if confidence < 0.3:
        return ReasoningMode.CRITIC
    if mode.mode == SystemMode.AUDIT:
        return ReasoningMode.RETRIEVAL
    return INTENT_TO_REASONING.get(perceived.intent, ReasoningMode.DIRECT)
