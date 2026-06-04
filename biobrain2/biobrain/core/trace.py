"""
biobrain.core.trace — Auditable pipeline trace
================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .signals import (
    RawInput, PerceivedInput, SalienceScore,
    MemoryResult, ExecutiveDecision, CognitiveResult,
    ActionResult, FeedbackResult, ModeState,
)
from .enums import ReflexVerdict


@dataclass
class ReflexResponse:
    """Output of the reflex/safety layer."""
    verdict: ReflexVerdict
    rule_triggered: str = ""
    reason: str = ""
    sanitized_content: Optional[str] = None
    route_target: Optional[str] = None


@dataclass
class PipelineTrace:
    """Full auditable trace of a single processing cycle."""
    raw_input: Optional[RawInput] = None
    perceived: Optional[PerceivedInput] = None
    salience: Optional[SalienceScore] = None
    reflex: Optional[ReflexResponse] = None
    memory: Optional[MemoryResult] = None
    decision: Optional[ExecutiveDecision] = None
    cognitive: Optional[CognitiveResult] = None
    action_results: list[ActionResult] = field(default_factory=list)
    feedback_results: list[FeedbackResult] = field(default_factory=list)
    mode_at_processing: Optional[ModeState] = None
    elapsed_ms: float = 0.0
    halted_at: Optional[str] = None
    halt_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logging/audit."""
        result: dict[str, Any] = {}
        for fld_name in self.__dataclass_fields__:
            val = getattr(self, fld_name)
            if val is None:
                continue
            if isinstance(val, list):
                result[fld_name] = [
                    {k: str(v) for k, v in item.__dict__.items()}
                    if hasattr(item, "__dict__") else str(item)
                    for item in val
                ]
            elif hasattr(val, "__dataclass_fields__"):
                result[fld_name] = {k: str(v) for k, v in val.__dict__.items()}
            elif hasattr(val, "value"):
                result[fld_name] = val.value
            else:
                result[fld_name] = val
        return result

    @property
    def audit_summary(self) -> str:
        """One-line audit summary."""
        parts = []
        if self.perceived:
            parts.append(f"intent={self.perceived.intent}")
            parts.append(f"op={self.perceived.operation_class.value}")
        if self.salience:
            parts.append(f"priority={self.salience.priority.name}")
            parts.append(f"risk={self.salience.risk_score}")
        if self.reflex and self.reflex.verdict != ReflexVerdict.PASS:
            parts.append(f"reflex={self.reflex.verdict.value}:{self.reflex.rule_triggered}")
        if self.decision:
            parts.append(f"reasoning={self.decision.chosen_reasoning.value}")
            if self.decision.inhibited_actions:
                parts.append(f"inhibited={len(self.decision.inhibited_actions)}")
        if self.action_results:
            ok = sum(1 for a in self.action_results if a.success)
            parts.append(f"actions={ok}/{len(self.action_results)}")
        parts.append(f"elapsed={self.elapsed_ms:.0f}ms")
        if self.halted_at:
            parts.append(f"HALTED@{self.halted_at}")
        return " | ".join(parts)
