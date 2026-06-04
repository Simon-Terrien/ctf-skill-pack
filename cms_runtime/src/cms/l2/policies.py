"""
Episode closure policies.

A closure policy answers the question: "given the current open episode
and the observation that just arrived, should we close the episode now?"

Per the ADR:
  - Start with the simplest policies; add sophistication only when
    real data shows it's needed.
  - Policies are pluggable so research can experiment with different
    strategies without changing the EpisodeService contract.

Policies in this slice
----------------------
  WindowedClosurePolicy   — close every N observations
  SurpriseClosurePolicy   — close when surprise exceeds threshold
  CompositeClosurePolicy  — combine multiple policies (OR semantics)

Surprise computation is deliberately simple here: we use an injectable
SurpriseScorer so research can swap in proper Lyapunov-based signals
later without touching the policy itself.
"""

from __future__ import annotations

from typing import Protocol

from cms.l1.observation import L1Observation


class SurpriseScorer(Protocol):
    """Computes a surprise score for the next observation given recent ones."""

    def score(
        self,
        next_obs: L1Observation,
        recent: list[L1Observation],
    ) -> float: ...


class ClosurePolicy(Protocol):
    """Decides whether to close an open episode."""

    def should_close(
        self,
        next_obs: L1Observation,
        open_obs: list[L1Observation],
    ) -> tuple[bool, str]:
        """Return (should_close, reason)."""
        ...


# ── Concrete policies ────────────────────────────────────────────────


class WindowedClosurePolicy:
    """Close after the open episode reaches max_size observations.

    The simplest possible policy — useful as a baseline and for tests.
    The closure is evaluated *after* the next observation would be added,
    so max_size=10 means episodes contain up to 10 observations.
    """

    def __init__(self, max_size: int = 10):
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self.max_size = max_size

    def should_close(
        self,
        next_obs: L1Observation,
        open_obs: list[L1Observation],
    ) -> tuple[bool, str]:
        # +1 because next_obs will be appended before closure is acted on
        if len(open_obs) + 1 >= self.max_size:
            return True, "window_full"
        return False, ""


class SurpriseClosurePolicy:
    """Close when the next observation's surprise exceeds threshold.

    Uses an injected SurpriseScorer to compute the signal. The default
    scorer (EuclideanSurpriseScorer) compares the next observation's
    CMS coords to the mean of the open episode.
    """

    def __init__(
        self,
        scorer: SurpriseScorer,
        threshold: float = 2.0,
        min_history: int = 3,
    ):
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if min_history < 1:
            raise ValueError("min_history must be >= 1")
        self.scorer = scorer
        self.threshold = threshold
        self.min_history = min_history

    def should_close(
        self,
        next_obs: L1Observation,
        open_obs: list[L1Observation],
    ) -> tuple[bool, str]:
        if len(open_obs) < self.min_history:
            return False, ""
        score = self.scorer.score(next_obs, open_obs)
        if score >= self.threshold:
            return True, f"surprise_spike(score={score:.3f})"
        return False, ""


class CompositeClosurePolicy:
    """Close if ANY of the wrapped policies says so (OR semantics)."""

    def __init__(self, policies: list[ClosurePolicy]):
        if not policies:
            raise ValueError("at least one policy required")
        self.policies = policies

    def should_close(
        self,
        next_obs: L1Observation,
        open_obs: list[L1Observation],
    ) -> tuple[bool, str]:
        reasons = []
        for policy in self.policies:
            close, reason = policy.should_close(next_obs, open_obs)
            if close:
                reasons.append(reason)
        if reasons:
            return True, " | ".join(reasons)
        return False, ""


# ── Default surprise scorer ──────────────────────────────────────────


class EuclideanSurpriseScorer:
    """Surprise = z-scored Euclidean distance from mean of recent CMS coords.

    Deliberately simple. Research-line code can replace this with a proper
    Lyapunov-based signal by implementing the SurpriseScorer protocol.
    """

    def __init__(self, history_window: int = 10):
        self.history_window = history_window

    def score(
        self,
        next_obs: L1Observation,
        recent: list[L1Observation],
    ) -> float:
        if not recent:
            return 0.0

        # Use last N observations as the comparison baseline
        window = recent[-self.history_window:]

        # Build per-dimension means across the window
        n_dims = next_obs.cms_dim
        if n_dims == 0:
            return 0.0

        # Mean and std across recent CMS coords (real and imag interleaved)
        recent_coords = [
            obs.cms_real + obs.cms_imag for obs in window
            if obs.cms_dim == n_dims
        ]
        if not recent_coords:
            return 0.0

        mean = [sum(col) / len(col) for col in zip(*recent_coords)]
        next_coords = next_obs.cms_real + next_obs.cms_imag

        # Euclidean distance from mean
        deviation = sum((n - m) ** 2 for n, m in zip(next_coords, mean)) ** 0.5

        # Z-score against historical deviations (if we have enough history)
        if len(recent_coords) < 3:
            return deviation

        historical_deviations = []
        for coords in recent_coords:
            d = sum((c - m) ** 2 for c, m in zip(coords, mean)) ** 0.5
            historical_deviations.append(d)

        hist_mean = sum(historical_deviations) / len(historical_deviations)
        hist_var = sum((d - hist_mean) ** 2 for d in historical_deviations) / len(historical_deviations)
        hist_std = hist_var ** 0.5 + 1e-8

        z = (deviation - hist_mean) / hist_std
        # Clip to keep policy decisions stable when history is degenerate
        return max(-10.0, min(10.0, z))
