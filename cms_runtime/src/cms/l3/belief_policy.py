"""
Belief update policy — thresholds and the staleness check.

All numerics live here, not scattered through the service. Defaults match
the locked Block 5 contract and are overridable via constructor injection.

Naming note: this module is BeliefStalenessPolicy, NOT BeliefDecayPolicy.
Per the locked vocabulary contract, "decay" implies continuous score
mutation. Block 5 does discrete status transitions only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(slots=True, frozen=True)
class BeliefThresholds:
    """Tunable thresholds for belief lifecycle transitions.

    All defaults match the locked Block 5 contract.
    """

    # ── support strength ─────────────────────────────────────────────
    # Minimum support_score for an evidence record to count toward
    # belief promotion. Below this, the evidence still gets recorded
    # in the ledger but does not advance status.
    min_supporting_strength: float = 0.3

    # ── tentative threshold ──────────────────────────────────────────
    # Minimum same-direction supporting evidence to enter tentative.
    tentative_min_support: int = 1

    # ── active threshold ─────────────────────────────────────────────
    active_min_support: int = 3
    active_min_confidence: float = 0.5
    active_window_days: int = 30  # support must be within this window

    # ── stale threshold ──────────────────────────────────────────────
    # Active belief becomes stale if no supporting evidence within
    # this window. NOT decay — discrete transition.
    stale_window_days: int = 14

    # ── invalidated threshold ────────────────────────────────────────
    # Belief becomes invalidated when:
    #   contradiction count > support count, OR
    #   contradiction burst within burst window
    invalidation_burst_count: int = 3
    invalidation_burst_window_days: int = 7

    # ── confidence model ─────────────────────────────────────────────
    # Confidence per support record (capped at 1.0 across the chain).
    confidence_per_support: float = 0.2
    confidence_penalty_per_contradiction: float = 0.15

    def __post_init__(self) -> None:
        if self.min_supporting_strength < 0 or self.min_supporting_strength > 1:
            raise ValueError("min_supporting_strength must be in [0, 1]")
        if self.tentative_min_support < 1:
            raise ValueError("tentative_min_support must be >= 1")
        if self.active_min_support < self.tentative_min_support:
            raise ValueError(
                "active_min_support must be >= tentative_min_support"
            )
        if not (0.0 <= self.active_min_confidence <= 1.0):
            raise ValueError("active_min_confidence must be in [0, 1]")
        if self.active_window_days < 1:
            raise ValueError("active_window_days must be >= 1")
        if self.stale_window_days < 1:
            raise ValueError("stale_window_days must be >= 1")
        if self.invalidation_burst_count < 1:
            raise ValueError("invalidation_burst_count must be >= 1")

    @property
    def stale_window(self) -> timedelta:
        return timedelta(days=self.stale_window_days)

    @property
    def active_window(self) -> timedelta:
        return timedelta(days=self.active_window_days)

    @property
    def invalidation_burst_window(self) -> timedelta:
        return timedelta(days=self.invalidation_burst_window_days)


def is_belief_stale(
    last_support_at: datetime | None,
    now: datetime,
    thresholds: BeliefThresholds,
) -> bool:
    """Return True iff an active belief should transition to stale.

    Pure function of timestamps and thresholds — no belief mutation here.
    The caller (BeliefService) decides what to do with this signal.
    """
    if last_support_at is None:
        # No support ever recorded — stale by default
        return True
    return (now - last_support_at) > thresholds.stale_window
