"""
biobrain.ops.health — Health checks and runtime metrics
=========================================================

Collects operational metrics from the event bus and provides
health status for monitoring systems.

Usage:
    from biobrain.ops.health import HealthMonitor

    monitor = HealthMonitor()
    brain.bus.subscribe(monitor.on_event)

    print(monitor.status())       # {"healthy": True, ...}
    print(monitor.metrics())      # counters, latencies, error rates
"""

from __future__ import annotations

import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..core.events import Event

logger = logging.getLogger("biobrain.ops.health")


@dataclass
class Metrics:
    """Runtime metrics collected from event bus."""
    total_inputs: int = 0
    total_traces: int = 0
    total_actions: int = 0
    total_tool_calls: int = 0
    total_reflex_blocks: int = 0
    total_reflex_escalations: int = 0
    total_inhibitions: int = 0
    total_errors: int = 0
    total_feedback_mismatches: int = 0

    # Latency tracking (last N)
    latencies_ms: list[float] = field(default_factory=list)

    # Per-stage counters
    stage_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Per-reasoning-mode counters
    reasoning_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Error categories
    error_categories: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # Mode time tracking
    mode_transitions: int = 0

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return sum(self.latencies_ms) / len(self.latencies_ms)

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def error_rate(self) -> float:
        if self.total_traces == 0:
            return 0.0
        return self.total_errors / self.total_traces

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_inputs": self.total_inputs,
            "total_traces": self.total_traces,
            "total_actions": self.total_actions,
            "total_tool_calls": self.total_tool_calls,
            "reflex_blocks": self.total_reflex_blocks,
            "reflex_escalations": self.total_reflex_escalations,
            "inhibitions": self.total_inhibitions,
            "errors": self.total_errors,
            "feedback_mismatches": self.total_feedback_mismatches,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "error_rate": round(self.error_rate, 4),
            "reasoning_modes": dict(self.reasoning_counts),
            "error_categories": dict(self.error_categories),
            "mode_transitions": self.mode_transitions,
        }


class HealthMonitor:
    """Collects metrics from the event bus and provides health status.

    Subscribe to the brain's event bus:
        brain.bus.subscribe(monitor.on_event)
    """

    MAX_LATENCIES = 200

    def __init__(self):
        self._metrics = Metrics()
        self._start_time = time.time()
        self._last_event_time = time.time()

    @property
    def m(self) -> Metrics:
        return self._metrics

    def on_event(self, event: Event) -> None:
        """Event bus subscriber callback."""
        self._last_event_time = time.time()
        self._metrics.stage_counts[event.stage] += 1
        data = event.data if isinstance(event.data, dict) else {}

        if event.stage == "ingest" and event.event_type == "input":
            self._metrics.total_inputs += 1

        elif event.stage == "reflex":
            if event.event_type == "block":
                self._metrics.total_reflex_blocks += 1
            elif event.event_type == "escalate":
                self._metrics.total_reflex_escalations += 1

        elif event.stage == "executive" and event.event_type == "decided":
            inhibited = data.get("inhibited", [])
            self._metrics.total_inhibitions += len(inhibited)
            reasoning = data.get("reasoning", "")
            if reasoning:
                self._metrics.reasoning_counts[reasoning] += 1

        elif event.stage == "cognition" and event.event_type == "reasoned":
            mode = data.get("mode", "")
            if mode:
                self._metrics.reasoning_counts[mode] += 1

        elif event.stage == "action" and event.event_type == "executed":
            self._metrics.total_actions += 1
            if data.get("type") == "tool_call":
                self._metrics.total_tool_calls += 1
            if not data.get("success", True):
                self._metrics.total_errors += 1
                cat = data.get("error_category", "unknown")
                if cat:
                    self._metrics.error_categories[cat] += 1

        elif event.stage == "feedback" and event.event_type == "mismatch":
            self._metrics.total_feedback_mismatches += 1

        elif event.stage == "modulation" and event.event_type == "auto_escalated":
            self._metrics.mode_transitions += 1

        elif event.stage == "pipeline" and event.event_type == "finalized":
            self._metrics.total_traces += 1
            elapsed = data.get("elapsed_ms", 0)
            if elapsed:
                self._metrics.latencies_ms.append(elapsed)
                if len(self._metrics.latencies_ms) > self.MAX_LATENCIES:
                    self._metrics.latencies_ms = self._metrics.latencies_ms[-self.MAX_LATENCIES:]

        elif event.stage == "pipeline" and event.event_type == "exception":
            self._metrics.total_errors += 1
            self._metrics.error_categories["exception"] += 1

    def status(self) -> dict[str, Any]:
        """Health status for monitoring systems."""
        uptime = time.time() - self._start_time
        idle = time.time() - self._last_event_time

        healthy = (
            self._metrics.error_rate < 0.5
            and idle < 600  # no events for 10 min = stale
        )

        return {
            "healthy": healthy,
            "uptime_s": round(uptime, 1),
            "idle_s": round(idle, 1),
            "traces": self._metrics.total_traces,
            "error_rate": round(self._metrics.error_rate, 4),
            "avg_latency_ms": round(self._metrics.avg_latency_ms, 1),
        }

    def metrics(self) -> dict[str, Any]:
        """Full metrics for dashboards."""
        return self._metrics.to_dict()

    def reset(self) -> None:
        """Reset all metrics."""
        self._metrics = Metrics()
        self._start_time = time.time()
