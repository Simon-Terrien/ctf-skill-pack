"""
biobrain.ops.export — Trace export for training rooms and external systems
============================================================================

Exports pipeline traces and orchestration results into formats
suitable for AISEC training rooms, CyberRange exercises, and
external analysis tools.

Formats:
  - JSONL (one record per trace, for log aggregation)
  - Markdown (human-readable run report)
  - AISEC exercise format (structured for training room rendering)
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from ..core.trace import PipelineTrace
from ..runtime.orchestrator import OrchestrationResult


def trace_to_jsonl(trace: PipelineTrace, session_id: str = "") -> str:
    """Export a single trace as a JSONL line."""
    record = trace.to_dict()
    record["session_id"] = session_id
    record["exported_at"] = time.time()
    return json.dumps(record, default=str, separators=(",", ":"))


def orchestration_to_jsonl(result: OrchestrationResult) -> list[str]:
    """Export an orchestration result as multiple JSONL lines."""
    lines = []
    header = {
        "type": "orchestration_header",
        "goal": result.goal,
        "completed": result.completed,
        "halt_reason": result.halt_reason,
        "total_steps": result.total_steps,
        "total_tool_calls": result.total_tool_calls,
        "total_replans": result.total_replans,
        "elapsed_ms": result.total_elapsed_ms,
        "exported_at": time.time(),
    }
    lines.append(json.dumps(header, default=str, separators=(",", ":")))

    for step in result.steps:
        step_record = {
            "type": "orchestration_step",
            "step": step.step_number,
            "observation": step.observation,
            "replanned": step.replanned,
            "halt_reason": step.halt_reason,
            "trace_summary": step.trace.audit_summary,
        }
        lines.append(json.dumps(step_record, default=str, separators=(",", ":")))

    return lines


def trace_to_markdown(trace: PipelineTrace) -> str:
    """Export a trace as a human-readable markdown report."""
    lines = ["# Pipeline Trace Report", ""]
    lines.append(f"**Summary:** {trace.audit_summary}")
    lines.append(f"**Elapsed:** {trace.elapsed_ms:.0f}ms")
    lines.append("")

    if trace.perceived:
        lines.append("## Perception")
        lines.append(f"- Intent: `{trace.perceived.intent}`")
        lines.append(f"- Classification: `{trace.perceived.classification}`")
        lines.append(f"- Operation: `{trace.perceived.operation_class.value}`")
        if trace.perceived.entities:
            lines.append(f"- Entities: {', '.join(trace.perceived.entities[:10])}")
        if trace.perceived.risk_indicators:
            lines.append(f"- Risk indicators: {', '.join(trace.perceived.risk_indicators)}")
        lines.append("")

    if trace.salience:
        lines.append("## Salience")
        lines.append(f"- Priority: `{trace.salience.priority.name}`")
        lines.append(f"- Risk score: `{trace.salience.risk_score}`")
        lines.append(f"- Confidence: `{trace.salience.confidence}`")
        lines.append("")

    if trace.reflex and trace.reflex.verdict.value != "pass":
        lines.append("## Reflex")
        lines.append(f"- Verdict: **{trace.reflex.verdict.value.upper()}**")
        lines.append(f"- Rule: `{trace.reflex.rule_triggered}`")
        lines.append(f"- Reason: {trace.reflex.reason}")
        lines.append("")

    if trace.decision:
        lines.append("## Executive Decision")
        lines.append(f"- Reasoning mode: `{trace.decision.chosen_reasoning.value}`")
        lines.append(f"- Actions: {', '.join(a.value for a in trace.decision.chosen_actions)}")
        if trace.decision.inhibited_actions:
            lines.append(f"- **Inhibited:** {', '.join(trace.decision.inhibited_actions)}")
        for note in trace.decision.policy_notes:
            lines.append(f"- Policy: {note}")
        lines.append("")

    if trace.cognitive:
        lines.append("## Cognition")
        lines.append(f"- Mode used: `{trace.cognitive.reasoning_mode_used.value}`")
        lines.append(f"- Confidence: `{trace.cognitive.confidence}`")
        if trace.cognitive.evidence:
            lines.append(f"- Evidence items: {len(trace.cognitive.evidence)}")
        if trace.cognitive.result:
            lines.append(f"- Result preview: {trace.cognitive.result[:300]}")
        lines.append("")

    if trace.action_results:
        lines.append("## Actions")
        for i, ar in enumerate(trace.action_results, 1):
            status = "✓" if ar.success else "✗"
            lines.append(f"{i}. {status} `{ar.request.action_type.value}`"
                        f" ({ar.execution_time_ms:.0f}ms)")
            if ar.tool_name:
                lines.append(f"   Tool: `{ar.tool_name}`")
            if ar.error:
                lines.append(f"   Error: {ar.error}")
        lines.append("")

    if trace.halted_at:
        lines.append("## Halt")
        lines.append(f"- Halted at: `{trace.halted_at}`")
        lines.append(f"- Reason: {trace.halt_reason}")

    return "\n".join(lines)


def orchestration_to_markdown(result: OrchestrationResult) -> str:
    """Export an orchestration result as a markdown report."""
    lines = ["# Orchestration Report", ""]
    lines.append(f"**Goal:** {result.goal}")
    lines.append(f"**Completed:** {'Yes' if result.completed else 'No'}")
    if result.halt_reason:
        lines.append(f"**Halt reason:** {result.halt_reason}")
    lines.append(f"**Steps:** {result.total_steps}")
    lines.append(f"**Tool calls:** {result.total_tool_calls}")
    lines.append(f"**Replans:** {result.total_replans}")
    lines.append(f"**Elapsed:** {result.total_elapsed_ms:.0f}ms")
    lines.append("")

    for step in result.steps:
        tag = " 🔄" if step.replanned else ""
        lines.append(f"## Step {step.step_number}{tag}")
        lines.append(f"**Observation:** {step.observation}")
        if step.halt_reason:
            lines.append(f"**Halted:** {step.halt_reason}")
        lines.append(f"**Trace:** {step.trace.audit_summary}")
        lines.append("")

    return "\n".join(lines)


def to_aisec_exercise(
    result: OrchestrationResult,
    exercise_id: str,
    room: str,
    difficulty: str = "intermediate",
) -> dict[str, Any]:
    """Export as AISEC training room exercise format.

    Compatible with CyberRange UI YAML-driven exercise rendering.
    """
    steps_data = []
    for step in result.steps:
        step_data: dict[str, Any] = {
            "step_number": step.step_number,
            "observation": step.observation,
            "replanned": step.replanned,
        }

        if step.trace.perceived:
            step_data["intent"] = step.trace.perceived.intent
            step_data["risks"] = step.trace.perceived.risk_indicators

        if step.trace.salience:
            step_data["risk_score"] = step.trace.salience.risk_score
            step_data["confidence"] = step.trace.salience.confidence

        if step.trace.reflex and step.trace.reflex.verdict.value != "pass":
            step_data["reflex"] = {
                "verdict": step.trace.reflex.verdict.value,
                "rule": step.trace.reflex.rule_triggered,
            }

        if step.trace.decision:
            step_data["reasoning"] = step.trace.decision.chosen_reasoning.value
            step_data["inhibited"] = step.trace.decision.inhibited_actions

        step_data["actions"] = [
            {
                "type": ar.request.action_type.value,
                "success": ar.success,
                "tool": ar.tool_name,
                "error": ar.error,
            }
            for ar in step.trace.action_results
        ]

        if step.halt_reason:
            step_data["halt"] = step.halt_reason

        steps_data.append(step_data)

    return {
        "exercise_id": exercise_id,
        "room": room,
        "difficulty": difficulty,
        "goal": result.goal,
        "completed": result.completed,
        "halt_reason": result.halt_reason,
        "total_steps": result.total_steps,
        "total_replans": result.total_replans,
        "elapsed_ms": result.total_elapsed_ms,
        "steps": steps_data,
        "exported_at": time.time(),
    }
