"""
biobrain.runtime.orchestrator — Bounded plan/act/observe/replan loop
======================================================================

The pipeline is single-pass. The orchestrator wraps it into a multi-step
agent loop with explicit guards:

    1. PLAN   — first turn determines strategy
    2. ACT    — execute via pipeline
    3. OBSERVE — verify results
    4. REPLAN  — adjust based on feedback
    5. REPEAT  — until goal met, budget exhausted, or guard fires

Guards:
  - max_steps: hard ceiling on iterations
  - max_tool_calls: ceiling on tool invocations
  - halt_on_escalation: stop if any step requires human review
  - halt_on_low_confidence: stop if cumulative confidence drops
  - timeout_seconds: wall-clock time limit

Usage:
    orchestrator = Orchestrator(brain, max_steps=5)
    result = orchestrator.run("pentest the auth endpoint, check tokens, generate report")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Callable

from ..core.enums import InputSource, SystemMode, ActionType, ReflexVerdict
from ..core.signals import ModeState
from ..core.trace import PipelineTrace

logger = logging.getLogger("biobrain.runtime.orchestrator")


@dataclass
class StepResult:
    """Result of a single orchestration step."""
    step_number: int
    trace: PipelineTrace
    observation: str  # what happened, summarized
    should_continue: bool = True
    halt_reason: str = ""
    replanned: bool = False  # was the remaining plan adjusted after this step


@dataclass
class OrchestrationResult:
    """Full result of an orchestration run."""
    steps: list[StepResult] = field(default_factory=list)
    goal: str = ""
    completed: bool = False
    halt_reason: str = ""
    total_steps: int = 0
    total_tool_calls: int = 0
    total_replans: int = 0
    total_elapsed_ms: float = 0.0

    @property
    def summary(self) -> str:
        parts = [
            f"goal='{self.goal[:60]}'",
            f"steps={self.total_steps}",
            f"tools={self.total_tool_calls}",
            f"replans={self.total_replans}",
            f"completed={self.completed}",
            f"elapsed={self.total_elapsed_ms:.0f}ms",
        ]
        if self.halt_reason:
            parts.append(f"halt={self.halt_reason}")
        return " | ".join(parts)

    @property
    def all_traces(self) -> list[PipelineTrace]:
        return [s.trace for s in self.steps]


# ─── Plan decomposition ──────────────────────────────────────────────────────

def default_planner(goal: str) -> list[str]:
    """Default step decomposition — splits on common delimiters.

    In production, replace with LLM-backed planning via:
        orchestrator = Orchestrator(brain, planner=llm_planner)
    """
    # Split on commas, "then", "and then", semicolons, numbered steps
    import re
    # Try numbered steps first: "1. do X  2. do Y  3. do Z"
    numbered = re.findall(r"\d+[\.\)]\s*(.+?)(?=\d+[\.\)]|$)", goal)
    if len(numbered) >= 2:
        return [s.strip() for s in numbered if s.strip()]

    # Try delimiter-based split
    parts = re.split(r"[;,]\s*(?:and\s+)?(?:then\s+)?|(?:\s+then\s+)", goal)
    if len(parts) >= 2:
        return [p.strip() for p in parts if p.strip()]

    # Single step
    return [goal.strip()]


Planner = Callable[[str], list[str]]
Replanner = Callable[[str, list[StepResult], list[str]], list[str]]


def default_replanner(
    goal: str, completed_steps: list[StepResult], remaining_steps: list[str]
) -> list[str]:
    """Default replanner: adjusts remaining steps based on failures.

    If the last step failed or was inhibited, insert a retry or skip.
    In production, replace with LLM-backed replanning.
    """
    if not completed_steps:
        return remaining_steps

    last = completed_steps[-1]

    # If last step was blocked or inhibited, skip remaining steps of similar type
    if last.halt_reason and ("block" in last.halt_reason or "inhibit" in last.halt_reason):
        return remaining_steps  # proceed with remaining, the blocking was specific

    # If last step had a failed action, insert a diagnostic step
    if last.trace.action_results:
        failed = [ar for ar in last.trace.action_results if not ar.success]
        if failed:
            error = failed[0].error or "unknown"
            diagnostic = f"diagnose failure: {error[:80]}"
            return [diagnostic] + remaining_steps

    return remaining_steps


class Orchestrator:
    """Bounded multi-step agent loop with replan capability.

    The REPLAN step observes what happened in the previous step and
    can adjust the remaining plan before continuing. This is what
    makes it an actual agent loop, not just a sequential executor.
    """

    def __init__(
        self,
        brain,
        max_steps: int = 10,
        max_tool_calls: int = 20,
        timeout_seconds: float = 300.0,
        halt_on_escalation: bool = True,
        halt_on_low_confidence: float = 0.2,
        planner: Optional[Planner] = None,
        replanner: Optional[Replanner] = None,
        wing: Optional[str] = None,
        room: Optional[str] = None,
    ):
        self.brain = brain
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.halt_on_escalation = halt_on_escalation
        self.halt_on_low_confidence = halt_on_low_confidence
        self.planner = planner or default_planner
        self.replanner = replanner or default_replanner
        self.wing = wing
        self.room = room

    def run(
        self,
        goal: str,
        source: InputSource = InputSource.USER,
        metadata: Optional[dict[str, Any]] = None,
    ) -> OrchestrationResult:
        """Execute a goal through the plan/act/observe/replan loop."""
        start = time.time()
        result = OrchestrationResult(goal=goal)
        base_meta = metadata or {}

        # ── 1. PLAN — decompose goal into steps ──────────────────────
        remaining = list(self.planner(goal))
        logger.info("Orchestrator: %d steps planned for: %s", len(remaining), goal[:80])

        tool_call_count = 0
        cumulative_confidence = 0.5
        step_num = 0

        # ── 2. EXECUTE — bounded loop with replan ────────────────────
        while remaining:
            step_num += 1
            step_content = remaining.pop(0)

            # Guard: max steps
            if step_num > self.max_steps:
                result.halt_reason = f"max_steps_exceeded:{self.max_steps}"
                break

            # Guard: timeout
            if (time.time() - start) > self.timeout_seconds:
                result.halt_reason = f"timeout:{self.timeout_seconds}s"
                break

            # Guard: tool call budget
            if tool_call_count >= self.max_tool_calls:
                result.halt_reason = f"max_tool_calls:{self.max_tool_calls}"
                break

            # Guard: cumulative confidence
            if cumulative_confidence < self.halt_on_low_confidence:
                result.halt_reason = f"low_confidence:{cumulative_confidence:.2f}"
                break

            # ── ACT — run through pipeline ────────────────────────────
            meta = {
                **base_meta,
                "orchestrator_step": step_num,
                "orchestrator_goal": goal,
            }
            if self.wing:
                meta.setdefault("wing", self.wing)
            if self.room:
                meta.setdefault("room", self.room)

            trace = self.brain.process(step_content, source=source, metadata=meta)

            # ── OBSERVE ───────────────────────────────────────────────
            observation = self._observe(trace)
            step_tool_calls = sum(
                1 for ar in trace.action_results
                if ar.request.action_type == ActionType.TOOL_CALL
            )
            tool_call_count += step_tool_calls

            if trace.salience:
                alpha = 0.4
                cumulative_confidence = (
                    alpha * trace.salience.confidence
                    + (1 - alpha) * cumulative_confidence
                )

            # Check halt conditions
            should_continue = True
            halt_reason = ""

            if self.halt_on_escalation and self._has_escalation(trace):
                should_continue = False
                halt_reason = "escalation_required"

            if trace.halted_at and trace.halted_at.startswith("reflex_"):
                should_continue = False
                halt_reason = trace.halted_at

            if trace.halted_at == "executive_inhibition":
                should_continue = False
                halt_reason = "fully_inhibited"

            # ── REPLAN — adjust remaining steps based on observation ──
            replanned = False
            if should_continue and remaining:
                new_remaining = self.replanner(goal, result.steps + [
                    StepResult(step_number=step_num, trace=trace,
                               observation=observation, should_continue=should_continue)
                ], remaining)
                if new_remaining != remaining:
                    remaining = new_remaining
                    replanned = True
                    result.total_replans += 1
                    logger.info("Orchestrator replanned: %d steps remaining", len(remaining))

            step_result = StepResult(
                step_number=step_num,
                trace=trace,
                observation=observation,
                should_continue=should_continue,
                halt_reason=halt_reason,
                replanned=replanned,
            )
            result.steps.append(step_result)

            logger.info(
                "Orchestrator step %d: %s | tools=%d | confidence=%.2f%s",
                step_num, observation[:60],
                step_tool_calls, cumulative_confidence,
                " [replanned]" if replanned else "",
            )

            if not should_continue:
                result.halt_reason = halt_reason
                break

        # ── 3. FINALIZE ──────────────────────────────────────────────
        result.total_steps = len(result.steps)
        result.total_tool_calls = tool_call_count
        result.total_elapsed_ms = (time.time() - start) * 1000
        result.completed = not result.halt_reason and not remaining

        logger.info("Orchestrator done: %s", result.summary)
        return result

    def _observe(self, trace: PipelineTrace) -> str:
        """Produce a human-readable observation from a trace."""
        parts = []

        if trace.halted_at:
            parts.append(f"halted:{trace.halted_at}")
            if trace.halt_reason:
                parts.append(trace.halt_reason[:80])
            return " — ".join(parts)

        if trace.perceived:
            parts.append(f"intent={trace.perceived.intent}")

        if trace.decision:
            parts.append(f"reasoning={trace.decision.chosen_reasoning.value}")
            if trace.decision.inhibited_actions:
                parts.append(f"inhibited={len(trace.decision.inhibited_actions)}")

        action_count = len(trace.action_results)
        ok_count = sum(1 for a in trace.action_results if a.success)
        if action_count:
            parts.append(f"actions={ok_count}/{action_count}")

        for fb in trace.feedback_results:
            if not fb.expectation_met:
                parts.append(f"error={fb.prediction_error:.2f}")
                break

        return " | ".join(parts) if parts else "no_observation"

    def _has_escalation(self, trace: PipelineTrace) -> bool:
        """Check if any step produced an escalation."""
        if trace.halted_at and "escalat" in trace.halted_at:
            return True
        for ar in trace.action_results:
            if ar.request.action_type == ActionType.ESCALATION:
                return True
        if trace.decision:
            for note in trace.decision.policy_notes:
                if "APPROVAL REQUIRED" in note:
                    return True
        return False
