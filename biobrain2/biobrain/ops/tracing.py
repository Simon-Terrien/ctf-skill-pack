"""
biobrain.ops.tracing — Structured tracing for full observability
==================================================================

Follows every signal through the pipeline with:
  - Span-based tracing (parent → child relationships)
  - Model call tracking (which model, latency, tokens)
  - Tool invocation tracking (which tool, args, result, sandbox status)
  - Decision tracking (what was inhibited, why)
  - Timing at every stage

Designed for:
  - Debug logging during development
  - Performance profiling
  - REX data collection (which model did what, how fast, how well)
  - AISEC training room playback

Usage:
    from biobrain.ops.tracing import Tracer

    tracer = Tracer()
    brain.bus.subscribe(tracer.on_event)

    # After processing:
    tracer.print_timeline()
    tracer.export_spans()
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from io import StringIO
from typing import Any, Optional, TextIO

from ..core.events import Event

logger = logging.getLogger("biobrain.ops.tracing")


@dataclass
class Span:
    """A single traced operation."""
    span_id: str
    stage: str
    event_type: str
    start_time: float
    end_time: float = 0.0
    duration_ms: float = 0.0
    session_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        parts = [f"{self.stage}.{self.event_type}"]
        if self.duration_ms:
            parts.append(f"{self.duration_ms:.1f}ms")

        # Key data points per stage
        d = self.data
        if self.stage == "ingest":
            parts.append(f"src={d.get('source', '?')}")
            parts.append(f"trust={d.get('trust', '?')}")
        elif self.stage == "perception":
            parts.append(f"intent={d.get('intent', '?')}")
            parts.append(f"op={d.get('operation', '?')}")
            risks = d.get("risks", [])
            if risks:
                parts.append(f"risks={risks}")
        elif self.stage == "attention":
            parts.append(f"prio={d.get('priority', '?')}")
            parts.append(f"risk={d.get('risk', '?')}")
            parts.append(f"conf={d.get('confidence', '?')}")
        elif self.stage == "reflex":
            parts.append(f"rule={d.get('rule', '?')}")
        elif self.stage == "memory":
            for k in ("working", "episodic", "semantic", "procedural"):
                v = d.get(k, 0)
                if v:
                    parts.append(f"{k}={v}")
        elif self.stage == "executive":
            parts.append(f"reasoning={d.get('reasoning', '?')}")
            inh = d.get("inhibited", [])
            if inh:
                parts.append(f"INHIBITED={len(inh)}")
        elif self.stage == "cognition":
            parts.append(f"mode={d.get('mode', '?')}")
            parts.append(f"conf={d.get('confidence', '?')}")
        elif self.stage == "action":
            parts.append(f"type={d.get('type', '?')}")
            parts.append(f"ok={d.get('success', '?')}")
            tool = d.get("tool", "")
            if tool:
                parts.append(f"tool={tool}")
            t = d.get("time_ms", 0)
            if t:
                parts.append(f"exec={t:.0f}ms")
        elif self.stage == "feedback":
            parts.append(f"err={d.get('prediction_error', '?')}")
        elif self.stage == "pipeline":
            if self.event_type == "finalized":
                parts.append(d.get("summary", ""))
            elif self.event_type == "exception":
                parts.append(f"ERR={d.get('type', '?')}: {d.get('message', '?')[:60]}")
        elif self.stage == "modulation":
            parts.append(f"→{d.get('new_mode', '?')}")

        return " | ".join(parts)


class Tracer:
    """Structured tracer that subscribes to the event bus.

    Collects spans for every pipeline event and provides
    timeline display, export, and analysis.
    """

    def __init__(self, stream: Optional[TextIO] = None, live: bool = False):
        """
        Args:
            stream: Output stream for live tracing (e.g. sys.stderr)
            live: If True, print each span as it arrives
        """
        self._spans: list[Span] = []
        self._stream = stream
        self._live = live
        self._span_counter = 0
        self._session_starts: dict[str, float] = {}

    def on_event(self, event: Event) -> None:
        """Event bus subscriber. Creates a span for each event."""
        self._span_counter += 1
        span_id = f"s{self._span_counter:05d}"

        # Track session start times for relative timing
        sid = event.session_id or "_default"
        if sid not in self._session_starts:
            self._session_starts[sid] = event.timestamp

        span = Span(
            span_id=span_id,
            stage=event.stage,
            event_type=event.event_type,
            start_time=event.timestamp,
            end_time=event.timestamp,
            session_id=event.session_id,
            data=event.data if isinstance(event.data, dict) else {"raw": str(event.data)[:200]},
        )

        # Calculate duration from data if available
        if isinstance(event.data, dict):
            if "elapsed_ms" in event.data:
                span.duration_ms = event.data["elapsed_ms"]
            elif "time_ms" in event.data:
                span.duration_ms = event.data["time_ms"]

        self._spans.append(span)

        if self._live and self._stream:
            rel_t = (event.timestamp - self._session_starts.get(sid, event.timestamp)) * 1000
            line = f"[{rel_t:8.1f}ms] {span.summary}\n"
            self._stream.write(line)
            self._stream.flush()

    def timeline(self, session_id: Optional[str] = None) -> str:
        """Generate a human-readable timeline of all spans."""
        spans = self._spans
        if session_id:
            spans = [s for s in spans if s.session_id == session_id]

        if not spans:
            return "No spans recorded."

        t0 = spans[0].start_time
        lines = ["# Pipeline Trace Timeline", ""]

        for span in spans:
            rel = (span.start_time - t0) * 1000
            indent = "  "
            # Indent by stage depth
            depth_map = {
                "ingest": 0, "perception": 0, "attention": 0, "reflex": 0,
                "modulation": 1, "memory": 1, "executive": 1,
                "cognition": 2, "action": 2, "feedback": 2,
                "pipeline": 0,
            }
            depth = depth_map.get(span.stage, 0)
            indent = "  " * depth

            lines.append(f"{rel:8.1f}ms {indent}{span.summary}")

        return "\n".join(lines)

    def print_timeline(self, session_id: Optional[str] = None) -> None:
        """Print timeline to stdout."""
        print(self.timeline(session_id))

    def export_spans(self) -> list[dict[str, Any]]:
        """Export all spans as dicts for analysis."""
        return [
            {
                "span_id": s.span_id,
                "stage": s.stage,
                "event_type": s.event_type,
                "start_time": s.start_time,
                "duration_ms": s.duration_ms,
                "session_id": s.session_id,
                "summary": s.summary,
                "data": s.data,
            }
            for s in self._spans
        ]

    def export_jsonl(self) -> str:
        """Export spans as JSONL."""
        lines = []
        for span_dict in self.export_spans():
            lines.append(json.dumps(span_dict, default=str, separators=(",", ":")))
        return "\n".join(lines)

    def stage_timing(self) -> dict[str, dict[str, float]]:
        """Aggregate timing by stage."""
        from collections import defaultdict
        timings: dict[str, list[float]] = defaultdict(list)

        for span in self._spans:
            if span.duration_ms > 0:
                timings[span.stage].append(span.duration_ms)

        result = {}
        for stage, durations in timings.items():
            result[stage] = {
                "count": len(durations),
                "total_ms": round(sum(durations), 1),
                "avg_ms": round(sum(durations) / len(durations), 1),
                "max_ms": round(max(durations), 1),
            }
        return result

    def model_calls(self) -> list[dict[str, Any]]:
        """Extract model call spans for REX analysis."""
        return [
            s.data for s in self._spans
            if s.stage == "cognition" and s.event_type == "reasoned"
        ]

    def tool_calls(self) -> list[dict[str, Any]]:
        """Extract tool call spans."""
        return [
            s.data for s in self._spans
            if s.stage == "action" and s.event_type == "executed"
        ]

    def inhibitions(self) -> list[dict[str, Any]]:
        """Extract inhibition events."""
        results = []
        for s in self._spans:
            if s.stage == "executive" and s.data.get("inhibited"):
                results.append(s.data)
        return results

    @property
    def span_count(self) -> int:
        return len(self._spans)

    def clear(self) -> None:
        self._spans.clear()
        self._span_counter = 0
        self._session_starts.clear()
