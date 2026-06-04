"""
biobrain.feedback — Validation, error correction, adaptation
===============================================================
"""

from __future__ import annotations

import logging
from typing import Optional

from ..core.enums import SystemMode
from ..core.signals import ActionResult, FeedbackResult, ModeState, MemoryItem
from ..core.enums import TrustLevel

logger = logging.getLogger("biobrain.feedback")


def verify(action_result: ActionResult, mode: Optional[ModeState] = None) -> FeedbackResult:
    """Verify an action result and determine if correction is needed."""
    mode = mode or ModeState()

    if not action_result.success:
        return _handle_failure(action_result, mode)

    output = action_result.output
    corrections = []
    prediction_error = 0.0

    if output is None or output == "" or output == {}:
        corrections.append("empty_output")
        prediction_error = 0.5

    if isinstance(output, dict) and output.get("escalation"):
        corrections.append("escalation_triggered")
        prediction_error = 0.3

    if mode.mode == SystemMode.AUDIT:
        evidence = action_result.request.cognitive_result.evidence
        if not evidence:
            corrections.append("audit_violation:no_evidence")
            prediction_error = max(prediction_error, 0.6)

    confidence_delta = _confidence_delta(prediction_error, corrections)
    should_retry = prediction_error >= 0.5 and mode.mode != SystemMode.BUDGET_CONSTRAINED

    return FeedbackResult(
        action_result=action_result,
        expectation_met=prediction_error < 0.3,
        prediction_error=round(prediction_error, 3),
        corrections=corrections,
        should_retry=should_retry and action_result.success,
        confidence_adjustment=round(confidence_delta, 3),
    )


def _handle_failure(action_result: ActionResult, mode: ModeState) -> FeedbackResult:
    error = action_result.error or "unknown"
    cat = action_result.error_category or "unknown"
    corrections = [f"action_failed:{cat}:{error}"]
    retryable = cat in ("timeout", "rate_limit", "connection")

    return FeedbackResult(
        action_result=action_result,
        expectation_met=False,
        prediction_error=1.0,
        corrections=corrections,
        should_retry=retryable,
        confidence_adjustment=-0.2,
    )


def _confidence_delta(prediction_error: float, corrections: list[str]) -> float:
    if prediction_error == 0.0 and not corrections:
        return 0.05
    if prediction_error > 0.7:
        return -0.15
    if prediction_error > 0.3:
        return -0.05
    return 0.0


def feedback_to_episodic(feedback: FeedbackResult) -> Optional[dict]:
    """Convert feedback into an episodic memory entry for learning."""
    if feedback.expectation_met and not feedback.corrections:
        return None

    action_type = feedback.action_result.request.action_type.value
    reasoning = feedback.action_result.request.cognitive_result.reasoning_mode_used.value

    lines = [
        f"Action: {action_type}",
        f"Reasoning: {reasoning}",
        f"Success: {feedback.action_result.success}",
        f"Prediction error: {feedback.prediction_error}",
    ]
    if feedback.corrections:
        lines.append(f"Corrections: {'; '.join(feedback.corrections)}")
    if feedback.action_result.error:
        lines.append(f"Error: {feedback.action_result.error}")

    return {
        "content": "\n".join(lines),
        "hall": "hall_events",
        "metadata": {
            "feedback_type": "correction" if feedback.corrections else "success",
            "prediction_error": feedback.prediction_error,
        },
    }
