"""
Episode service — manages open episodes and closes them via policy.

Responsibility (single):
  Track per-session open episodes. When a new observation arrives,
  consult the closure policy. If the policy says close, build an
  L2Episode from the open observations, persist it, start a new
  open episode containing the new observation. Otherwise append.

Out of scope for this slice:
  - filing evidence from closed episodes
  - updating beliefs from closed episodes
  - retrieval over episodes
  - context assembly

Per the ADR: episodes are persisted, but their consumption by higher
layers belongs to Block 3+.

Threading note
--------------
Per-session open-episode state is held in memory (self._open). Concurrent
ingestion for the same session from multiple threads is not supported.
Multi-tenant isolation across users is fine — open state is keyed by
(user_id, session_id).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l2.policies import ClosurePolicy

if TYPE_CHECKING:
    from cms.storage.episode_store import EpisodeStore


# Session key type — (user_id, session_id)
_SessionKey = tuple[str, str]


class EpisodeService:
    """Manage open episodes and close them per policy."""

    def __init__(
        self,
        store: "EpisodeStore",
        policy: ClosurePolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ):
        """
        Parameters
        ----------
        store
            Persistence backend for closed episodes.
        policy
            Closure policy. Pluggable per ADR.
        clock
            Datetime factory. Injectable for tests.
        id_factory
            Episode id factory. Injectable for tests.
        """
        self._store = store
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)

        # Per-session open episode state (in-memory)
        self._open: dict[_SessionKey, list[L1Observation]] = {}

    # ── main API ─────────────────────────────────────────────────────

    def update(self, obs: L1Observation) -> L2Episode | None:
        """Process the next observation. Return a closed episode if one was created.

        Behavior:
          - Consult the closure policy with the open episode + this obs.
          - If close: build episode from open observations, persist,
            start fresh open episode containing this obs, return closed.
          - If no close: append to open, return None.

        Note that this means the *triggering* observation belongs to the
        NEW episode, not the closed one. The closure decision is "this
        observation is different enough that the prior regime ended."
        """
        key = (obs.user_id, obs.session_id)
        open_obs = self._open.setdefault(key, [])

        should_close, reason = self._policy.should_close(obs, open_obs)

        closed_episode: L2Episode | None = None

        if should_close and open_obs:
            # Build and persist the closed episode from accumulated observations
            closed_episode = self._build_episode(open_obs, reason)
            self._store.save(closed_episode)
            # Reset open state — the triggering observation starts a new episode
            self._open[key] = [obs]
        else:
            # Append to the open episode
            open_obs.append(obs)

        return closed_episode

    def flush(self, user_id: str, session_id: str) -> L2Episode | None:
        """Force-close any open episode for a session.

        Useful at session end. Returns the closed episode, or None if
        no open episode existed.
        """
        key = (user_id, session_id)
        open_obs = self._open.get(key, [])
        if not open_obs:
            return None

        closed = self._build_episode(open_obs, "flush")
        self._store.save(closed)
        del self._open[key]
        return closed

    def open_size(self, user_id: str, session_id: str) -> int:
        """Number of observations in the currently-open episode for a session."""
        return len(self._open.get((user_id, session_id), []))

    def reset(self, user_id: str, session_id: str) -> None:
        """Drop the open episode without persisting it (for testing/admin)."""
        self._open.pop((user_id, session_id), None)

    # ── episode construction ─────────────────────────────────────────

    def _build_episode(
        self,
        observations: list[L1Observation],
        reason: str,
    ) -> L2Episode:
        first = observations[0]
        last = observations[-1]

        return L2Episode(
            episode_id=self._id_factory(),
            user_id=first.user_id,
            session_id=first.session_id,
            created_at=self._clock(),
            start_at=first.created_at,
            end_at=last.created_at,
            obs_ids=[o.obs_id for o in observations],
            trajectory_signature={},  # populated by future signature service
            surprise_score=0.0,
            drift_score=0.0,
            confidence_score=0.0,
            closure_reason=reason,
            metadata={},
        )
