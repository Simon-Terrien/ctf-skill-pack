"""
biobrain.core.signals — Typed data contracts between all modules
=================================================================

Every module communicates through typed signals, not raw dicts.
This is the nervous system: structured, typed, auditable.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .enums import (
    InputSource, TrustLevel, Priority, ReasoningMode,
    SystemMode, ActionType, ReflexVerdict, OperationClass,
)


def _signal_id() -> str:
    return str(uuid.uuid4())[:12]


def _now() -> float:
    return time.time()


# ─── Input signals ────────────────────────────────────────────────────────────

@dataclass
class RawInput:
    """Output of ingest/. Raw, uninterpreted signal."""
    content: str
    source: InputSource
    trust: TrustLevel = TrustLevel.UNTRUSTED
    metadata: dict[str, Any] = field(default_factory=dict)
    signal_id: str = field(default_factory=_signal_id)
    timestamp: float = field(default_factory=_now)


@dataclass
class PerceivedInput:
    """Output of perception/. Structured, classified signal."""
    raw: RawInput
    intent: str = ""
    entities: list[str] = field(default_factory=list)
    language: str = "en"
    risk_indicators: list[str] = field(default_factory=list)
    classification: str = "general"
    operation_class: OperationClass = OperationClass.READ
    normalized_content: str = ""
    signal_id: str = field(default_factory=_signal_id)


@dataclass
class SalienceScore:
    """Output of attention/. What matters, what doesn't."""
    perceived: PerceivedInput
    priority: Priority = Priority.NORMAL
    risk_score: float = 0.0
    confidence: float = 0.5
    novelty: float = 0.5
    conflicts: list[str] = field(default_factory=list)
    suggested_reasoning: ReasoningMode = ReasoningMode.DIRECT
    signal_id: str = field(default_factory=_signal_id)


# ─── Memory signals ──────────────────────────────────────────────────────────

@dataclass
class MemoryItem:
    """A single memory recall result with provenance."""
    text: str
    memory_type: str  # working, episodic, semantic, procedural, kg
    source: str = ""
    wing: str = ""
    room: str = ""
    trust: TrustLevel = TrustLevel.TRUSTED
    similarity: float = 0.0
    timestamp: Optional[str] = None
    freshness: float = 1.0  # 1.0 = current, decays with age
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryQuery:
    """Request TO the memory system."""
    query: str
    memory_types: list[str] = field(default_factory=lambda: ["all"])
    wing: Optional[str] = None
    room: Optional[str] = None
    n_results: int = 5
    signal_id: str = field(default_factory=_signal_id)


@dataclass
class MemoryResult:
    """Response FROM the memory system."""
    query: MemoryQuery
    working: list[MemoryItem] = field(default_factory=list)
    episodic: list[MemoryItem] = field(default_factory=list)
    semantic: list[MemoryItem] = field(default_factory=list)
    procedural: list[MemoryItem] = field(default_factory=list)
    kg_facts: list[dict[str, Any]] = field(default_factory=list)
    signal_id: str = field(default_factory=_signal_id)

    @property
    def all_items(self) -> list[MemoryItem]:
        return self.working + self.episodic + self.semantic + self.procedural


# ─── Decision signals ────────────────────────────────────────────────────────

@dataclass
class ExecutiveDecision:
    """Output of executive/. What to do and how."""
    salience: SalienceScore
    memory: Optional[MemoryResult] = None
    chosen_reasoning: ReasoningMode = ReasoningMode.DIRECT
    chosen_actions: list[ActionType] = field(default_factory=list)
    inhibited_actions: list[str] = field(default_factory=list)
    policy_notes: list[str] = field(default_factory=list)
    delegation: Optional[str] = None
    signal_id: str = field(default_factory=_signal_id)


@dataclass
class CognitiveResult:
    """Output of cognition/. The actual reasoning product."""
    decision: ExecutiveDecision
    reasoning_mode_used: ReasoningMode = ReasoningMode.DIRECT
    result: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.5
    reasoning_trace: list[str] = field(default_factory=list)
    signal_id: str = field(default_factory=_signal_id)


# ─── Action signals ──────────────────────────────────────────────────────────

@dataclass
class ActionRequest:
    """Request TO the action/motor system."""
    action_type: ActionType
    cognitive_result: CognitiveResult
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False
    signal_id: str = field(default_factory=_signal_id)


@dataclass
class ActionResult:
    """Output of action/. What actually happened."""
    request: ActionRequest
    success: bool = False
    output: Any = None
    error: Optional[str] = None
    error_category: str = ""  # timeout, permission, validation, unknown
    execution_time_ms: float = 0.0
    tool_name: str = ""
    signal_id: str = field(default_factory=_signal_id)


@dataclass
class FeedbackResult:
    """Output of feedback/. Did the action match expectations?"""
    action_result: ActionResult
    expectation_met: bool = True
    prediction_error: float = 0.0
    corrections: list[str] = field(default_factory=list)
    should_retry: bool = False
    confidence_adjustment: float = 0.0
    signal_id: str = field(default_factory=_signal_id)


# ─── Cross-cutting state ─────────────────────────────────────────────────────

@dataclass
class ModeState:
    """Global modulatory state."""
    mode: SystemMode = SystemMode.NORMAL
    risk_level: float = 0.0
    confidence_floor: float = 0.3
    autonomy_ceiling: float = 0.8
    budget_remaining: Optional[float] = None
    active_constraints: list[str] = field(default_factory=list)
    mode_reason: str = ""


@dataclass
class PolicyRule:
    """A single structured policy rule."""
    domain: str  # e.g. "security", "operations"
    operation: OperationClass
    effect: str  # "allow", "deny", "require_approval"
    condition: str = ""  # optional condition description
    modes: list[SystemMode] = field(default_factory=list)  # modes where this applies


@dataclass
class IdentityState:
    """Stable self-model."""
    persona: str = ""
    role: str = ""
    mandate: str = ""
    allowed_domains: list[str] = field(default_factory=list)
    forbidden_operations: list[OperationClass] = field(default_factory=list)
    require_approval_for: list[OperationClass] = field(default_factory=list)
    require_evidence_for: list[str] = field(default_factory=list)
    policies: list[PolicyRule] = field(default_factory=list)
    communication_style: str = ""
