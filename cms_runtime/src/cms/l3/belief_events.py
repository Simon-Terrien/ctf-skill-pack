"""
Belief transition events — Block 6.

Per the locked contract:
  - callback hook, no new persistence table
  - 6 event types
  - emit only on real state transitions and explicit recompute
  - NullEventHandler and LoggingEventHandler shipped

Events carry enough context to reconstruct what happened without
re-querying the database. Consumers that want persistence implement
their own handler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

# Event type literals — these strings are the public contract.
EventType = Literal[
    "belief_tentative_created",
    "belief_activated",
    "belief_staled",
    "belief_invalidated",
    "belief_recomputed",
    "belief_scoped_created",
]

VALID_EVENT_TYPES: frozenset[str] = frozenset({
    "belief_tentative_created",
    "belief_activated",
    "belief_staled",
    "belief_invalidated",
    "belief_recomputed",
    "belief_scoped_created",
})


@dataclass(slots=True)
class BeliefEvent:
    """Structured event emitted on belief state transitions.

    Per the locked contract:
        - belief_id
        - user_id
        - dimension
        - context_key
        - status_before
        - status_after
        - triggered_by_evidence_ids

    Plus event_type and timestamp for observability.
    """
    event_type: str
    timestamp: datetime
    belief_id: str
    user_id: str
    dimension: str
    context_key: str | None
    status_before: str | None       # None for *_created events
    status_after: str
    triggered_by_evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.event_type not in VALID_EVENT_TYPES:
            raise ValueError(
                f"invalid event_type {self.event_type!r}. "
                f"Valid: {sorted(VALID_EVENT_TYPES)}"
            )


class BeliefEventHandler(Protocol):
    """Receiver of belief transition events.

    Implementations decide whether to log, persist, ignore, etc.
    """

    def __call__(self, event: BeliefEvent) -> None: ...


class NullEventHandler:
    """Default handler — drops every event.

    The runtime ships with this so BeliefService doesn't require an
    explicit handler choice from callers that don't care about events.
    """

    def __call__(self, event: BeliefEvent) -> None:
        return None


class LoggingEventHandler:
    """Emits each event via the standard logging module.

    Default logger name is 'cms.belief.events'. The log level is INFO.
    Callers that want different routing can construct one with their
    own logger.
    """

    def __init__(self, logger: logging.Logger | None = None,
                 level: int = logging.INFO):
        self._log = logger or logging.getLogger("cms.belief.events")
        self._level = level

    def __call__(self, event: BeliefEvent) -> None:
        self._log.log(
            self._level,
            "%s: belief_id=%s user=%s dimension=%s context=%s "
            "status_before=%s status_after=%s triggered_by=%s",
            event.event_type,
            event.belief_id,
            event.user_id,
            event.dimension,
            event.context_key,
            event.status_before,
            event.status_after,
            event.triggered_by_evidence_ids,
        )
