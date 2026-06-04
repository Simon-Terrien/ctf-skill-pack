"""
biobrain.runtime.session — Multi-turn session with persistent state
=====================================================================

The pipeline is single-call. The session wraps it to maintain:
  - Working memory across turns
  - Mode history
  - Approval state (pending confirmations)
  - Turn-level trace history
  - Cumulative confidence tracking

Usage:
    session = Session(brain)
    r1 = session.turn("scan the auth endpoint")
    r2 = session.turn("now check the session tokens")
    r3 = session.turn("generate the report")
    print(session.summary)
"""

from __future__ import annotations

import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..core.enums import InputSource, SystemMode, ActionType
from ..core.signals import ModeState, IdentityState
from ..core.trace import PipelineTrace

logger = logging.getLogger("biobrain.runtime.session")


@dataclass
class ApprovalRequest:
    """A pending action waiting for human confirmation."""
    request_id: str
    action_description: str
    turn_number: int
    requested_at: float
    approved: Optional[bool] = None
    resolved_at: Optional[float] = None


@dataclass
class SessionState:
    """Snapshot of session at any point — serializable for persistence."""
    session_id: str
    turns_completed: int = 0
    total_actions: int = 0
    total_inhibitions: int = 0
    total_escalations: int = 0
    cumulative_confidence: float = 0.5
    mode_transitions: list[dict[str, Any]] = field(default_factory=list)
    pending_approvals: list[ApprovalRequest] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


class Session:
    """Multi-turn session wrapping a BioBrain instance.

    Maintains state between process() calls, tracks approvals,
    and provides session-level audit summaries.
    """

    def __init__(self, brain, wing: Optional[str] = None, room: Optional[str] = None):
        """
        Args:
            brain: A BioBrain instance (or anything with .process(), .mode_manager, .memory)
            wing: Default MemPalace wing for this session
            room: Default MemPalace room for this session
        """
        self.brain = brain
        self.wing = wing
        self.room = room
        self.session_id = str(uuid.uuid4())[:12]

        self._state = SessionState(session_id=self.session_id)
        self._traces: list[PipelineTrace] = []
        self._start_time = time.time()

        logger.info("Session %s started (wing=%s, room=%s)", self.session_id, wing, room)

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def traces(self) -> list[PipelineTrace]:
        return list(self._traces)

    @property
    def turn_count(self) -> int:
        return self._state.turns_completed

    @property
    def pending_approvals(self) -> list[ApprovalRequest]:
        return [a for a in self._state.pending_approvals if a.approved is None]

    def turn(
        self,
        content: str,
        source: InputSource = InputSource.USER,
        metadata: Optional[dict[str, Any]] = None,
    ) -> PipelineTrace:
        """Execute one turn of the session.

        Automatically injects session context (wing, room, turn number)
        into the metadata and updates session state from the trace.
        """
        meta = {
            "session_id": self.session_id,
            "turn": self._state.turns_completed + 1,
            **(metadata or {}),
        }
        if self.wing and "wing" not in meta:
            meta["wing"] = self.wing
        if self.room and "room" not in meta:
            meta["room"] = self.room

        # Inject any resolved approvals as confirmation
        if self._has_resolved_approval():
            meta["confirmed"] = True

        trace = self.brain.process(content, source=source, metadata=meta)
        self._update_state(trace)
        self._traces.append(trace)

        return trace

    def approve(self, request_id: Optional[str] = None) -> bool:
        """Approve a pending approval. If no ID given, approve the most recent."""
        pending = self.pending_approvals
        if not pending:
            return False

        target = None
        if request_id:
            target = next((a for a in pending if a.request_id == request_id), None)
        else:
            target = pending[-1]  # most recent

        if target:
            target.approved = True
            target.resolved_at = time.time()
            logger.info("Approval granted: %s", target.request_id)
            return True
        return False

    def deny(self, request_id: Optional[str] = None) -> bool:
        """Deny a pending approval."""
        pending = self.pending_approvals
        if not pending:
            return False

        target = None
        if request_id:
            target = next((a for a in pending if a.request_id == request_id), None)
        else:
            target = pending[-1]

        if target:
            target.approved = False
            target.resolved_at = time.time()
            logger.info("Approval denied: %s", target.request_id)
            return True
        return False

    def set_mode(self, mode: SystemMode, reason: str) -> ModeState:
        """Explicitly transition system mode within the session."""
        new_state = self.brain.mode_manager.transition(mode, reason)
        self._state.mode_transitions.append({
            "turn": self._state.turns_completed,
            "mode": mode.value,
            "reason": reason,
            "timestamp": time.time(),
        })
        return new_state

    def reset_mode(self, reason: str = "session_reset") -> ModeState:
        """Reset to NORMAL mode."""
        return self.set_mode(SystemMode.NORMAL, reason)

    def _update_state(self, trace: PipelineTrace) -> None:
        """Update session state from a completed trace."""
        self._state.turns_completed += 1
        self._state.last_activity = time.time()

        # Count actions
        self._state.total_actions += len(trace.action_results)

        # Count inhibitions
        if trace.decision and trace.decision.inhibited_actions:
            self._state.total_inhibitions += len(trace.decision.inhibited_actions)

        # Count escalations
        if trace.halted_at and "escalat" in trace.halted_at:
            self._state.total_escalations += 1
        for ar in trace.action_results:
            if ar.request.action_type == ActionType.ESCALATION:
                self._state.total_escalations += 1

        # Track pending approvals from policy notes
        if trace.decision:
            for note in trace.decision.policy_notes:
                if "APPROVAL REQUIRED" in note:
                    self._state.pending_approvals.append(ApprovalRequest(
                        request_id=str(uuid.uuid4())[:8],
                        action_description=note,
                        turn_number=self._state.turns_completed,
                        requested_at=time.time(),
                    ))

        # Update cumulative confidence (exponential moving average)
        if trace.salience:
            alpha = 0.3
            self._state.cumulative_confidence = (
                alpha * trace.salience.confidence
                + (1 - alpha) * self._state.cumulative_confidence
            )

        # Record mode transitions
        for entry in self.brain.mode_manager.history:
            if entry not in [m.get("raw") for m in self._state.mode_transitions]:
                self._state.mode_transitions.append({
                    "turn": self._state.turns_completed,
                    **entry,
                })

    def _has_resolved_approval(self) -> bool:
        """Check if there's a recently approved request."""
        return any(
            a.approved is True and a.resolved_at
            and time.time() - a.resolved_at < 60
            for a in self._state.pending_approvals
        )

    @property
    def summary(self) -> str:
        """Session-level audit summary."""
        elapsed = time.time() - self._start_time
        parts = [
            f"session={self.session_id}",
            f"turns={self._state.turns_completed}",
            f"actions={self._state.total_actions}",
            f"inhibitions={self._state.total_inhibitions}",
            f"escalations={self._state.total_escalations}",
            f"confidence={self._state.cumulative_confidence:.2f}",
            f"pending_approvals={len(self.pending_approvals)}",
            f"mode={self.brain.mode_manager.state.mode.value}",
            f"elapsed={elapsed:.1f}s",
        ]
        return " | ".join(parts)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._start_time
