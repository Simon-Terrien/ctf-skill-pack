"""
biobrain.core.events — Observable event bus for signal flow
=============================================================

Every pipeline stage can emit events. External consumers can subscribe
to tap into any stage for logging, dashboards, debugging, or replay.

Usage:
    from biobrain.core.events import EventBus, Event

    bus = EventBus()

    # Subscribe to all events
    bus.subscribe(lambda e: print(f"[{e.stage}] {e.event_type}: {e.summary}"))

    # Subscribe to specific stages
    bus.subscribe(lambda e: log_risk(e), stage="attention")

    # In pipeline code:
    bus.emit(Event(stage="reflex", event_type="block", data=reflex_response))
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("biobrain.events")


@dataclass
class Event:
    """A single pipeline event."""
    stage: str          # which module: ingest, perception, attention, reflex, etc.
    event_type: str     # what happened: input, classified, scored, blocked, decided, etc.
    data: Any = None    # the signal or payload
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def summary(self) -> str:
        if isinstance(self.data, dict):
            return str(self.data)[:120]
        elif hasattr(self.data, "audit_summary"):
            return self.data.audit_summary
        return str(self.data)[:120] if self.data else ""


Subscriber = Callable[[Event], None]


class EventBus:
    """Publish/subscribe event bus for pipeline observability.

    Subscribers can filter by stage. All events are also stored
    in a bounded buffer for replay/inspection.
    """

    def __init__(self, buffer_size: int = 500):
        self._subscribers: list[tuple[Subscriber, Optional[str]]] = []
        self._buffer: list[Event] = []
        self._buffer_size = buffer_size

    def subscribe(self, callback: Subscriber, stage: Optional[str] = None) -> None:
        """Subscribe to events. Optionally filter by stage."""
        self._subscribers.append((callback, stage))

    def unsubscribe(self, callback: Subscriber) -> None:
        """Remove a subscriber."""
        self._subscribers = [(cb, s) for cb, s in self._subscribers if cb is not callback]

    def emit(self, event: Event) -> None:
        """Emit an event to all matching subscribers."""
        self._buffer.append(event)
        if len(self._buffer) > self._buffer_size:
            self._buffer = self._buffer[-self._buffer_size:]

        for callback, stage_filter in self._subscribers:
            if stage_filter is None or stage_filter == event.stage:
                try:
                    callback(event)
                except Exception as e:
                    logger.warning("Event subscriber error: %s", e)

    def emit_simple(
        self, stage: str, event_type: str, data: Any = None, session_id: str = ""
    ) -> None:
        """Convenience: emit without constructing Event manually."""
        self.emit(Event(stage=stage, event_type=event_type, data=data, session_id=session_id))

    @property
    def events(self) -> list[Event]:
        """All buffered events."""
        return list(self._buffer)

    def events_for_stage(self, stage: str) -> list[Event]:
        """Filter buffered events by stage."""
        return [e for e in self._buffer if e.stage == stage]

    def events_since(self, timestamp: float) -> list[Event]:
        """Events after a given timestamp."""
        return [e for e in self._buffer if e.timestamp >= timestamp]

    def clear(self) -> None:
        """Clear the event buffer."""
        self._buffer.clear()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
