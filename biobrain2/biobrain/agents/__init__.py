"""
biobrain.agents — Specialized agents for the agentic runtime
================================================================

Each agent wraps a role-specific model call with structured
prompting, output parsing, and quality checks.

Agents are NOT autonomous — they are invoked by the orchestrator
as steps in a plan. The executive decides which agent to call;
the agent executes its specialty.

Agents:
  PlannerAgent  — decomposes goals into ordered steps
  CoderAgent    — generates, fixes, and reviews code
  CriticAgent   — reviews outputs for errors, gaps, contradictions
  SecurityAgent — reviews for security issues (OWASP-aware)
  BenchmarkAgent — runs model comparisons on tasks
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..core.enums import ReasoningMode, InputSource
from ..core.signals import (
    RawInput, PerceivedInput, SalienceScore, ExecutiveDecision,
    CognitiveResult, ModeState, MemoryResult, MemoryQuery,
)
from ..cognition.router import ModelRouter, RouteResult

logger = logging.getLogger("biobrain.agents")


@dataclass
class AgentResult:
    """Structured output from any agent."""
    agent_name: str
    role: str
    model_used: str
    output: str
    structured_data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgent:
    """Base class for all specialized agents."""

    ROLE: str = "fast"
    NAME: str = "base"

    def __init__(self, router: ModelRouter, mode: Optional[ModeState] = None):
        self.router = router
        self.mode = mode or ModeState()

    def _make_decision(
        self, content: str, reasoning: ReasoningMode = ReasoningMode.DIRECT,
        memory: Optional[MemoryResult] = None,
    ) -> ExecutiveDecision:
        """Build a minimal ExecutiveDecision for routing."""
        raw = RawInput(content=content, source=InputSource.INTERNAL)
        perceived = PerceivedInput(raw=raw, normalized_content=content)
        salience = SalienceScore(perceived=perceived)
        return ExecutiveDecision(
            salience=salience,
            memory=memory,
            chosen_reasoning=reasoning,
        )

    def _route(self, content: str, memory: Optional[MemoryResult] = None) -> RouteResult:
        """Route to this agent's role model."""
        decision = self._make_decision(content, memory=memory)
        return self.router.route(self.ROLE, decision, self.mode)

    def _to_result(self, route_result: RouteResult, structured: Optional[dict] = None) -> AgentResult:
        """Convert a RouteResult into an AgentResult."""
        return AgentResult(
            agent_name=self.NAME,
            role=self.ROLE,
            model_used=route_result.model_spec.model,
            output=route_result.cognitive_result.result,
            structured_data=structured or {},
            confidence=route_result.cognitive_result.confidence,
            latency_ms=route_result.latency_ms,
        )


class PlannerAgent(BaseAgent):
    """Decomposes goals into ordered, actionable steps."""

    ROLE = "planner"
    NAME = "planner"

    def plan(self, goal: str, context: str = "") -> AgentResult:
        """Generate a plan for a goal.

        Returns structured steps in the output and as structured_data.
        """
        prompt = (
            f"Decompose the following goal into concrete, ordered steps.\n"
            f"Each step should be a single actionable task.\n"
            f"Format: numbered list (1. 2. 3. etc.)\n\n"
            f"Goal: {goal}"
        )
        if context:
            prompt += f"\n\nContext:\n{context}"

        route = self._route(prompt)

        # Parse numbered steps from output
        steps = []
        for line in route.cognitive_result.result.split("\n"):
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith("-")):
                # Strip numbering
                clean = line.lstrip("0123456789.-) ").strip()
                if clean:
                    steps.append(clean)

        return self._to_result(route, {"steps": steps, "steps_count": len(steps)})


class CoderAgent(BaseAgent):
    """Generates, fixes, and reviews code."""

    ROLE = "coder"
    NAME = "coder"

    def generate(self, task: str, language: str = "python", context: str = "") -> AgentResult:
        """Generate code for a task."""
        prompt = (
            f"Write {language} code for the following task.\n"
            f"Include docstrings and type hints.\n"
            f"Task: {task}"
        )
        if context:
            prompt += f"\n\nContext:\n{context}"

        route = self._route(prompt)
        return self._to_result(route, {"language": language, "task": task})

    def fix(self, code: str, error: str, language: str = "python") -> AgentResult:
        """Fix code given an error."""
        prompt = (
            f"Fix the following {language} code.\n"
            f"Error: {error}\n\n"
            f"Code:\n```{language}\n{code}\n```\n\n"
            f"Return only the fixed code."
        )
        route = self._route(prompt)
        return self._to_result(route, {"fix_type": "error", "language": language})

    def review(self, code: str, language: str = "python") -> AgentResult:
        """Review code for quality issues."""
        prompt = (
            f"Review the following {language} code for:\n"
            f"1. Bugs and logical errors\n"
            f"2. Security issues\n"
            f"3. Performance problems\n"
            f"4. Code quality and readability\n\n"
            f"Code:\n```{language}\n{code}\n```"
        )
        route = self._route(prompt)
        return self._to_result(route, {"review_type": "quality", "language": language})


class CriticAgent(BaseAgent):
    """Reviews outputs for errors, gaps, and contradictions."""

    ROLE = "critic"
    NAME = "critic"

    def critique(self, content: str, original_task: str = "") -> AgentResult:
        """Critique a piece of output."""
        prompt = (
            f"Critically review the following output.\n"
            f"Identify:\n"
            f"1. Factual errors or unsupported claims\n"
            f"2. Logical gaps or contradictions\n"
            f"3. Missing important considerations\n"
            f"4. Confidence assessment (strong/moderate/weak)\n\n"
            f"Output to review:\n{content}"
        )
        if original_task:
            prompt += f"\n\nOriginal task: {original_task}"

        route = self._route(prompt)
        return self._to_result(route, {"critique_type": "general"})

    def verify(self, claim: str, evidence: list[str]) -> AgentResult:
        """Verify a claim against evidence."""
        evidence_text = "\n".join(f"- {e}" for e in evidence)
        prompt = (
            f"Verify the following claim against the provided evidence.\n\n"
            f"Claim: {claim}\n\n"
            f"Evidence:\n{evidence_text}\n\n"
            f"Verdict: supported / partially supported / contradicted / insufficient evidence"
        )
        route = self._route(prompt)
        return self._to_result(route, {"verification": True})


class SecurityAgent(BaseAgent):
    """Reviews for security issues — OWASP-aware."""

    ROLE = "security"
    NAME = "security_reviewer"

    def review_code(self, code: str, language: str = "python") -> AgentResult:
        """Security review of code."""
        prompt = (
            f"Perform a security review of the following {language} code.\n"
            f"Check for OWASP Top 10 issues:\n"
            f"- Injection (SQLi, XSS, command injection)\n"
            f"- Broken authentication\n"
            f"- Sensitive data exposure\n"
            f"- Security misconfiguration\n"
            f"- Insecure deserialization\n\n"
            f"For each finding, provide:\n"
            f"- Severity (critical/high/medium/low)\n"
            f"- WSTG ID if applicable\n"
            f"- Remediation\n\n"
            f"Code:\n```{language}\n{code}\n```"
        )
        route = self._route(prompt)
        return self._to_result(route, {"review_type": "security", "language": language})

    def review_config(self, config: str, config_type: str = "yaml") -> AgentResult:
        """Security review of configuration."""
        prompt = (
            f"Review the following {config_type} configuration for security issues.\n"
            f"Check for:\n"
            f"- Exposed credentials or secrets\n"
            f"- Insecure defaults\n"
            f"- Missing security controls\n"
            f"- Overly permissive settings\n\n"
            f"Configuration:\n```{config_type}\n{config}\n```"
        )
        route = self._route(prompt)
        return self._to_result(route, {"review_type": "config_security"})


# ─── Agent registry ──────────────────────────────────────────────────────────

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "planner": PlannerAgent,
    "coder": CoderAgent,
    "critic": CriticAgent,
    "security": SecurityAgent,
}


def create_agent(
    name: str, router: ModelRouter, mode: Optional[ModeState] = None
) -> BaseAgent:
    """Create an agent by name."""
    cls = AGENT_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown agent: {name}. Available: {list(AGENT_REGISTRY.keys())}")
    return cls(router=router, mode=mode)
