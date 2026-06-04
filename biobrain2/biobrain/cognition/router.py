"""
biobrain.cognition.router — Multi-model task routing
=======================================================

Routes tasks to specialized local models based on role.
Instead of one monolithic LLM, the router assigns:

  planning    → qwen3.6 (strong reasoning, broad context)
  coding      → qwen3-coder (code-specialized, tool-use)
  critic      → qwen36-a3b (analytical, contradiction detection)
  reasoning   → zaya1-8b (deep reasoning, chain-of-thought)
  fast        → qwen3.5:4b (classification, simple tasks)
  security    → qwen3.6 (security-aware, policy-grounded)

The router is configured via YAML or Settings and integrates
with the existing cognition layer through the Reasoner protocol.

Usage:
    from biobrain.cognition.router import ModelRouter

    router = ModelRouter.from_config({
        "planner": {"model": "qwen3.6", "temperature": 0.3},
        "coder":   {"model": "qwen3-coder", "temperature": 0.2},
        "critic":  {"model": "qwen36-a3b-q4km", "temperature": 0.4},
    })

    # Route by role
    result = router.route("coding", decision, mode)

    # Auto-route by reasoning mode
    result = router.auto_route(decision, mode)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..core.enums import ReasoningMode, SystemMode
from ..core.signals import ExecutiveDecision, CognitiveResult, ModeState

logger = logging.getLogger("biobrain.cognition.router")


@dataclass
class ModelSpec:
    """Specification for a single model endpoint."""
    model: str
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.3
    max_tokens: int = 2048
    api_key: Optional[str] = None
    timeout_seconds: float = 120.0

    @property
    def display(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass
class RouteResult:
    """Result of a model routing decision."""
    role: str
    model_spec: ModelSpec
    cognitive_result: CognitiveResult
    latency_ms: float = 0.0
    tokens_estimated: int = 0


# ─── Default role → reasoning mode mapping ───────────────────────────────────

ROLE_TO_REASONING: dict[str, ReasoningMode] = {
    "planner": ReasoningMode.PLANNING,
    "coder": ReasoningMode.DIRECT,
    "critic": ReasoningMode.CRITIC,
    "reasoning": ReasoningMode.CAUSAL,
    "fast": ReasoningMode.DIRECT,
    "security": ReasoningMode.CHECKLIST,
    "retrieval": ReasoningMode.RETRIEVAL,
}

REASONING_TO_ROLE: dict[ReasoningMode, str] = {
    ReasoningMode.PLANNING: "planner",
    ReasoningMode.DIRECT: "fast",
    ReasoningMode.CHECKLIST: "security",
    ReasoningMode.CAUSAL: "reasoning",
    ReasoningMode.CRITIC: "critic",
    ReasoningMode.RETRIEVAL: "retrieval",
    ReasoningMode.SIMULATION: "reasoning",
}

# ─── Default model assignments ───────────────────────────────────────────────

DEFAULT_MODELS: dict[str, dict[str, Any]] = {
    "planner":   {"model": "qwen3.6", "temperature": 0.3},
    "coder":     {"model": "qwen3-coder", "temperature": 0.2},
    "critic":    {"model": "qwen36-a3b-q4km", "temperature": 0.4},
    "reasoning": {"model": "zaya1-8b", "temperature": 0.3},
    "fast":      {"model": "qwen3.5:4b", "temperature": 0.2, "max_tokens": 512},
    "security":  {"model": "qwen3.6", "temperature": 0.3},
    "retrieval": {"model": "qwen3.6", "temperature": 0.2},
}


class ModelRouter:
    """Routes tasks to specialized models based on role.

    Integrates with the cognition layer: the executive decides
    the reasoning mode, the router picks the best model for it.
    """

    def __init__(self, models: Optional[dict[str, ModelSpec]] = None):
        self._models: dict[str, ModelSpec] = models or {}
        self._call_history: list[dict[str, Any]] = []

    @classmethod
    def from_config(cls, config: dict[str, dict[str, Any]]) -> ModelRouter:
        """Create router from config dict."""
        models = {}
        for role, spec in config.items():
            models[role] = ModelSpec(**spec)
        return cls(models=models)

    @classmethod
    def from_defaults(cls) -> ModelRouter:
        """Create router with default model assignments."""
        return cls.from_config(DEFAULT_MODELS)

    def get_model(self, role: str) -> ModelSpec:
        """Get the model spec for a role. Falls back to 'fast'."""
        return self._models.get(role, self._models.get("fast", ModelSpec(model="qwen3.5:4b")))

    def route(
        self,
        role: str,
        decision: ExecutiveDecision,
        mode: ModeState,
    ) -> RouteResult:
        """Route a task to a specific role's model."""
        spec = self.get_model(role)
        reasoning_mode = ROLE_TO_REASONING.get(role, ReasoningMode.DIRECT)

        start = time.time()
        cognitive = self._call_model(spec, decision, mode, reasoning_mode)
        latency = (time.time() - start) * 1000

        # Estimate tokens from result length
        tokens_est = len(cognitive.result) // 4 if cognitive.result else 0

        result = RouteResult(
            role=role,
            model_spec=spec,
            cognitive_result=cognitive,
            latency_ms=round(latency, 1),
            tokens_estimated=tokens_est,
        )

        # Record for benchmarking
        self._call_history.append({
            "role": role,
            "model": spec.model,
            "reasoning_mode": reasoning_mode.value,
            "latency_ms": result.latency_ms,
            "tokens_estimated": result.tokens_estimated,
            "confidence": cognitive.confidence,
            "timestamp": time.time(),
        })

        logger.info(
            "Routed %s → %s (%s, %.0fms)",
            role, spec.model, reasoning_mode.value, latency,
        )
        return result

    def auto_route(
        self,
        decision: ExecutiveDecision,
        mode: ModeState,
    ) -> RouteResult:
        """Auto-route based on the executive's chosen reasoning mode."""
        reasoning = decision.chosen_reasoning
        role = REASONING_TO_ROLE.get(reasoning, "fast")

        # Mode overrides
        if mode.mode == SystemMode.AUDIT:
            role = "reasoning"  # deep analysis in audit mode
        elif mode.mode == SystemMode.INCIDENT:
            role = "fast"  # speed priority

        # Intent overrides
        intent = decision.salience.perceived.intent
        if intent in ("creation", "remediation") and role == "fast":
            role = "coder"

        return self.route(role, decision, mode)

    def _call_model(
        self,
        spec: ModelSpec,
        decision: ExecutiveDecision,
        mode: ModeState,
        reasoning_mode: ReasoningMode,
    ) -> CognitiveResult:
        """Call the LLM via the existing LLMReasoner infrastructure."""
        try:
            from .adapters.llm import LLMReasoner
            reasoner = LLMReasoner(
                provider=spec.provider,
                model=spec.model,
                base_url=spec.base_url,
                api_key=spec.api_key,
                temperature=spec.temperature,
                max_tokens=spec.max_tokens,
                timeout_seconds=spec.timeout_seconds,
            )
            # Override the decision's reasoning mode to match the role
            decision_copy = ExecutiveDecision(
                salience=decision.salience,
                memory=decision.memory,
                chosen_reasoning=reasoning_mode,
                chosen_actions=decision.chosen_actions,
                inhibited_actions=decision.inhibited_actions,
                policy_notes=decision.policy_notes,
            )
            return reasoner.run(decision_copy, mode)
        except Exception as e:
            logger.error("Model call failed (%s): %s", spec.display, e)
            return CognitiveResult(
                decision=decision,
                reasoning_mode_used=reasoning_mode,
                result=f"[ROUTER ERROR: {spec.display}: {e}]",
                confidence=0.0,
                reasoning_trace=[f"router_error: {e}"],
            )

    @property
    def call_history(self) -> list[dict[str, Any]]:
        return list(self._call_history)

    @property
    def available_roles(self) -> list[str]:
        return list(self._models.keys())

    def model_summary(self) -> dict[str, str]:
        """Which model is assigned to which role."""
        return {role: spec.display for role, spec in self._models.items()}
