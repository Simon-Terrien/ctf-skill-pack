"""
biobrain.core.audit — Structured JSON audit logging
=====================================================

Produces audit-ready JSON records from pipeline traces and events.
Designed for compliance, incident review, and AISEC training rooms.

Usage:
    from biobrain.core.audit import AuditLogger

    audit = AuditLogger("/var/log/biobrain/audit.jsonl")
    brain.bus.subscribe(audit.on_event)  # auto-log all events

    # Or log traces directly:
    audit.log_trace(trace)
    audit.log_session(session)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional, TextIO

from .events import Event
from .trace import PipelineTrace

logger = logging.getLogger("biobrain.audit")


class AuditLogger:
    """Writes structured JSON audit records to a file or stream.

    Each record is one JSON object per line (JSONL format).
    Compatible with: jq, Elasticsearch, Splunk, CloudWatch, etc.
    """

    def __init__(
        self,
        output: Optional[str] = None,
        stream: Optional[TextIO] = None,
        include_events: bool = True,
        include_traces: bool = True,
    ):
        """
        Args:
            output: Path to JSONL file (creates/appends)
            stream: Alternative output stream (e.g. sys.stdout)
            include_events: Log individual pipeline events
            include_traces: Log complete pipeline traces
        """
        self._file: Optional[TextIO] = None
        self._stream = stream
        self._output_path = output
        self.include_events = include_events
        self.include_traces = include_traces

        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            self._file = open(output, "a", buffering=1)  # line-buffered

    def _write(self, record: dict[str, Any]) -> None:
        """Write a single audit record."""
        line = json.dumps(record, default=str, separators=(",", ":"))
        if self._file:
            self._file.write(line + "\n")
        if self._stream:
            self._stream.write(line + "\n")

    def on_event(self, event: Event) -> None:
        """Event bus subscriber callback. Logs events as audit records."""
        if not self.include_events:
            return
        self._write({
            "type": "event",
            "ts": event.timestamp,
            "stage": event.stage,
            "event": event.event_type,
            "session": event.session_id,
            "data": _safe_serialize(event.data),
        })

    def log_trace(self, trace: PipelineTrace, session_id: str = "") -> None:
        """Log a complete pipeline trace as a single audit record."""
        if not self.include_traces:
            return

        record: dict[str, Any] = {
            "type": "trace",
            "ts": time.time(),
            "session": session_id,
            "elapsed_ms": trace.elapsed_ms,
            "halted_at": trace.halted_at,
            "halt_reason": trace.halt_reason,
            "summary": trace.audit_summary,
        }

        if trace.perceived:
            record["intent"] = trace.perceived.intent
            record["classification"] = trace.perceived.classification
            record["operation"] = trace.perceived.operation_class.value
            record["entities"] = trace.perceived.entities[:10]
            record["risks"] = trace.perceived.risk_indicators

        if trace.salience:
            record["priority"] = trace.salience.priority.name
            record["risk_score"] = trace.salience.risk_score
            record["confidence"] = trace.salience.confidence

        if trace.reflex:
            record["reflex_verdict"] = trace.reflex.verdict.value
            record["reflex_rule"] = trace.reflex.rule_triggered

        if trace.decision:
            record["reasoning"] = trace.decision.chosen_reasoning.value
            record["actions"] = [a.value for a in trace.decision.chosen_actions]
            record["inhibited"] = trace.decision.inhibited_actions
            record["policy_notes"] = trace.decision.policy_notes

        if trace.cognitive:
            record["reasoning_confidence"] = trace.cognitive.confidence
            record["evidence_count"] = len(trace.cognitive.evidence)

        record["action_results"] = [
            {
                "type": ar.request.action_type.value,
                "success": ar.success,
                "tool": ar.tool_name,
                "error": ar.error,
                "error_category": ar.error_category,
                "time_ms": ar.execution_time_ms,
            }
            for ar in trace.action_results
        ]

        record["feedback"] = [
            {
                "met": fb.expectation_met,
                "error": fb.prediction_error,
                "retry": fb.should_retry,
                "corrections": fb.corrections,
            }
            for fb in trace.feedback_results
        ]

        if trace.mode_at_processing:
            record["mode"] = trace.mode_at_processing.mode.value

        self._write(record)

    def log_session(self, session) -> None:
        """Log a session summary as an audit record."""
        self._write({
            "type": "session",
            "ts": time.time(),
            "session_id": session.session_id,
            "turns": session.state.turns_completed,
            "actions": session.state.total_actions,
            "inhibitions": session.state.total_inhibitions,
            "escalations": session.state.total_escalations,
            "confidence": session.state.cumulative_confidence,
            "pending_approvals": len(session.pending_approvals),
            "elapsed_s": session.elapsed_seconds,
            "mode": session.brain.mode_manager.state.mode.value,
            "summary": session.summary,
        })

    def close(self) -> None:
        """Flush and close the output file."""
        if self._file:
            self._file.flush()
            self._file.close()
            self._file = None


def _safe_serialize(data: Any) -> Any:
    """Safely serialize data for JSON output."""
    if data is None:
        return None
    if isinstance(data, (str, int, float, bool)):
        return data
    if isinstance(data, dict):
        return {str(k): _safe_serialize(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_safe_serialize(v) for v in data]
    if hasattr(data, "value"):  # Enum
        return data.value
    return str(data)
