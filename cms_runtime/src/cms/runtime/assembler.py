"""
State assembler — Block 4 + Block 5.

Responsibilities (single):
    Compose RuntimeStateView from persisted state, computing
    lightweight signals/counts/freshness flags and surfacing
    active and tentative beliefs.

Out of scope:
    - belief composition or mutation (read-only — guardrail B)
    - belief-to-belief reasoning
    - decay
    - LLM/agent/dashboard formatting

Per the Block 4 contract, freshness is operational, not semantic.
Per the Block 5 contract, this assembler reads beliefs but never
computes or mutates them. BeliefService owns all belief writes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from cms.runtime.retrieval import RetrievalService
from cms.runtime.state import RuntimeStateView

if TYPE_CHECKING:
    from cms.storage.belief_store import BeliefStore


# Freshness thresholds — boundaries beyond which "recent" stops being true.
# Tunable via constructor; defaults are deliberate but not load-bearing.
DEFAULT_RECENT_OBSERVATION_SECONDS = 60.0 * 5     # 5 minutes
DEFAULT_RECENT_EPISODE_SECONDS = 60.0 * 30        # 30 minutes
DEFAULT_RECENT_EVIDENCE_SECONDS = 60.0 * 60       # 1 hour


class StateAssembler:
    """Build canonical RuntimeStateView from persisted state."""

    def __init__(
        self,
        retrieval_service: RetrievalService,
        *,
        belief_store: "BeliefStore | None" = None,
        clock: Callable[[], datetime] | None = None,
        recent_observation_seconds: float = DEFAULT_RECENT_OBSERVATION_SECONDS,
        recent_episode_seconds: float = DEFAULT_RECENT_EPISODE_SECONDS,
        recent_evidence_seconds: float = DEFAULT_RECENT_EVIDENCE_SECONDS,
    ):
        """
        Parameters
        ----------
        retrieval_service
            Required. Provides recent observations/episodes/evidence.
        belief_store
            Optional. If provided, active and tentative beliefs are
            surfaced in the view. If None (Block 4 mode), belief lists
            are empty.
        """
        self._retrieval = retrieval_service
        self._belief_store = belief_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._recent_obs_seconds = recent_observation_seconds
        self._recent_ep_seconds = recent_episode_seconds
        self._recent_ev_seconds = recent_evidence_seconds

    def build(
        self,
        user_id: str,
        session_id: str,
        *,
        scope: str | None = None,
        subscope: str | None = None,
        source_kind: str | None = None,
    ) -> RuntimeStateView:
        """Compose a canonical state view.

        scope/subscope/source_kind narrow evidence retrieval. They do
        not affect observation, episode, or belief retrieval.
        """
        # Gather persisted state via retrieval service
        recent_obs = self._retrieval.get_recent_observations(user_id, session_id)
        recent_eps = self._retrieval.get_recent_episodes(user_id, session_id)
        evidence = self._retrieval.search_evidence(
            user_id,
            scope=scope,
            subscope=subscope,
            source_kind=source_kind,
        )

        # Beliefs (read-only — never mutated here)
        # Block 6: split into global vs scoped buckets per locked contract.
        active_beliefs_global: list = []
        active_beliefs_scoped: list = []
        tentative_beliefs_global: list = []
        tentative_beliefs_scoped: list = []
        stale_count = 0
        invalidated_count = 0
        if self._belief_store is not None:
            all_beliefs = self._belief_store.list_for_user(user_id)
            for b in all_beliefs:
                if b.status == "active":
                    if b.is_global:
                        active_beliefs_global.append(b)
                    else:
                        active_beliefs_scoped.append(b)
                elif b.status == "tentative":
                    if b.is_global:
                        tentative_beliefs_global.append(b)
                    else:
                        tentative_beliefs_scoped.append(b)
                elif b.status == "stale":
                    stale_count += 1
                elif b.status == "invalidated":
                    invalidated_count += 1

        current_obs = recent_obs[0] if recent_obs else None
        now = self._clock()

        # ── signals (numeric ages) ──────────────────────────────────
        signals: dict[str, float] = {}
        if recent_obs:
            signals["latest_observation_age_seconds"] = (
                now - recent_obs[0].created_at
            ).total_seconds()
        if recent_eps:
            signals["latest_episode_age_seconds"] = (
                now - recent_eps[0].end_at
            ).total_seconds()
        if evidence:
            signals["latest_evidence_age_seconds"] = (
                now - evidence[0].created_at
            ).total_seconds()

        # ── counts ──────────────────────────────────────────────────
        active_total = len(active_beliefs_global) + len(active_beliefs_scoped)
        tentative_total = len(tentative_beliefs_global) + len(tentative_beliefs_scoped)
        counts: dict[str, int] = {
            "recent_observations": len(recent_obs),
            "recent_episodes": len(recent_eps),
            "retrieved_evidence": len(evidence),
            "active_beliefs": active_total,
            "active_beliefs_global": len(active_beliefs_global),
            "active_beliefs_scoped": len(active_beliefs_scoped),
            "tentative_beliefs": tentative_total,
            "tentative_beliefs_global": len(tentative_beliefs_global),
            "tentative_beliefs_scoped": len(tentative_beliefs_scoped),
            "stale_beliefs": stale_count,
            "invalidated_beliefs": invalidated_count,
        }

        # ── freshness flags ─────────────────────────────────────────
        flags: dict[str, bool] = {
            "has_recent_observations": self._is_fresh(
                signals.get("latest_observation_age_seconds"),
                self._recent_obs_seconds,
            ),
            "has_recent_episodes": self._is_fresh(
                signals.get("latest_episode_age_seconds"),
                self._recent_ep_seconds,
            ),
            "has_recent_evidence": self._is_fresh(
                signals.get("latest_evidence_age_seconds"),
                self._recent_ev_seconds,
            ),
            "has_active_beliefs": active_total > 0,
            "has_active_global_beliefs": len(active_beliefs_global) > 0,
            "has_active_scoped_beliefs": len(active_beliefs_scoped) > 0,
        }

        return RuntimeStateView(
            user_id=user_id,
            session_id=session_id,
            current_observation=current_obs,
            recent_observations=recent_obs,
            recent_episodes=recent_eps,
            retrieved_evidence=evidence,
            active_beliefs_global=active_beliefs_global,
            active_beliefs_scoped=active_beliefs_scoped,
            tentative_beliefs_global=tentative_beliefs_global,
            tentative_beliefs_scoped=tentative_beliefs_scoped,
            signals=signals,
            counts=counts,
            freshness_flags=flags,
        )

    @staticmethod
    def _is_fresh(age_seconds: float | None, threshold: float) -> bool:
        """True iff age is present and within threshold."""
        return age_seconds is not None and age_seconds <= threshold
