"""
L2 Episode — local regime in a conversation trajectory.

An L2Episode is the immutable record of a coherent stretch of conversation:
a window of observations during which the user's behavior stays within
some local mode. Episode boundaries occur when the underlying dynamics
shift enough to warrant treating what comes next as a new regime.

Per the ADR sequencing decision:
  - Episodes persist alongside observations (this slice).
  - Evidence filing from episodes is deferred to Block 3.
  - Belief updates from episodes are deferred to Block 5.
  - Episodes carry a trajectory_signature, but its contents are
    intentionally free-form (Dict[str, float]) — we let the signature
    schema emerge from the data rather than locking it down now.

Identity rules
--------------
  - episode_id is unique per episode
  - obs_ids preserves arrival order (not just set membership)
  - start_at <= end_at always
  - end_at is the created_at of the last observation in the episode
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class L2Episode:
    """Immutable record of one episode (closed local regime)."""

    # ── identity ────────────────────────────────────────────────────
    episode_id: str
    user_id: str
    session_id: str
    created_at: datetime           # when the episode record was created
    start_at: datetime             # timestamp of first observation
    end_at: datetime               # timestamp of last observation

    # ── membership ──────────────────────────────────────────────────
    # Observation ids in arrival order. NOT a set — order matters.
    obs_ids: list[str]

    # ── dynamics summary ────────────────────────────────────────────
    # Free-form per the ADR — schema may evolve as patterns emerge.
    # Typical contents (when computed): mean_surprise, drift_magnitude,
    # mean_pragmatic_ratio, mean_velocity, etc.
    trajectory_signature: dict[str, float] = field(default_factory=dict)

    # ── episode-level scores ────────────────────────────────────────
    # Aggregate signals about the episode itself.
    # All default to 0.0 — only meaningful when the closure policy
    # populates them.
    surprise_score: float = 0.0    # How surprising the episode was overall
    drift_score: float = 0.0       # How much the trajectory wandered
    confidence_score: float = 0.0  # How confident we are in this signature

    # ── classification ──────────────────────────────────────────────
    # Free string per ADR — no fixed taxonomy at this layer.
    # Closure policies tag what triggered the close ("window_full",
    # "surprise_spike", "drift_threshold", "session_end", etc.)
    closure_reason: str = "unknown"

    # ── extension ───────────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── invariants ──────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if not self.episode_id:
            raise ValueError("episode_id is required")
        if not self.user_id:
            raise ValueError("user_id is required")
        if not self.session_id:
            raise ValueError("session_id is required")
        if not self.obs_ids:
            raise ValueError("episode must contain at least one observation")
        if self.start_at > self.end_at:
            raise ValueError(
                f"start_at ({self.start_at}) must not exceed end_at ({self.end_at})"
            )

    # ── convenience ─────────────────────────────────────────────────

    @property
    def length(self) -> int:
        """Number of observations in the episode."""
        return len(self.obs_ids)

    @property
    def duration_seconds(self) -> float:
        """Wall-clock duration of the episode."""
        return (self.end_at - self.start_at).total_seconds()
