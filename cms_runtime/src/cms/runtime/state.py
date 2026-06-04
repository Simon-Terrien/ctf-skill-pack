"""
Runtime state types — the canonical, consumer-neutral view assembled by Block 4.

Per the Block 4 contract:
  - one canonical RuntimeStateView, not per-consumer views
  - embedded records (not just ids) so consumers don't need a second store call
  - signals / counts / freshness_flags split for readability

Per the ADR:
  - consumer-neutral: no LLM prompt format, no agent routing, no UI rendering
  - no belief logic, no contradiction logic, no decay
  - this is a *view* over persisted state, not a transformation of it

Block 5 (beliefs) will likely add `active_beliefs` here — but the dataclass
extension stays additive, no field renames.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l3.belief import ProfileBelief
from cms.l3.evidence import MemoryEvidence


@dataclass(slots=True)
class RetrievalPolicy:
    """Tuning knobs for retrieval. Defaults match the Block 4 contract."""

    observation_limit: int = 5
    episode_limit: int = 3
    evidence_limit: int = 5

    def __post_init__(self) -> None:
        if self.observation_limit < 0:
            raise ValueError("observation_limit must be >= 0")
        if self.episode_limit < 0:
            raise ValueError("episode_limit must be >= 0")
        if self.evidence_limit < 0:
            raise ValueError("evidence_limit must be >= 0")


@dataclass(slots=True)
class RuntimeStateView:
    """Canonical internal state assembled from persisted records.

    This is the consumer-neutral handoff point. Consumers (LLM context
    builders, agent routers, dashboards) project from this view. The
    runtime itself does not pick a representation.

    Embedded records carry their own ids — call `obs.obs_id`, etc.
    """

    # ── identity ────────────────────────────────────────────────────
    user_id: str
    session_id: str

    # ── current turn anchor ─────────────────────────────────────────
    # The most recent observation in the session, if any.
    current_observation: L1Observation | None

    # ── recent context (newest first) ───────────────────────────────
    recent_observations: list[L1Observation]
    recent_episodes: list[L2Episode]
    retrieved_evidence: list[MemoryEvidence]

    # ── belief state (Block 5 + Block 6) ────────────────────────────
    # Active beliefs are surfaced as the primary belief surface;
    # tentative beliefs are exposed separately so consumers can decide
    # whether to use them. Stale and invalidated beliefs are NOT
    # surfaced here — they remain in the store for audit but are not
    # operational truth.
    #
    # Block 6 split: global (context_key=None) vs scoped (context_key
    # != None) beliefs are surfaced separately. Per guardrail B,
    # neither dominates the other and there is no implicit
    # reconciliation. Consumers explicitly pick which to consult.
    #
    # The legacy `active_beliefs` and `tentative_beliefs` properties
    # below return the global lists, preserving Block 5 callers that
    # didn't think about scope. Scoped consumers use the explicit
    # *_scoped fields.
    active_beliefs_global: list[ProfileBelief] = field(default_factory=list)
    active_beliefs_scoped: list[ProfileBelief] = field(default_factory=list)
    tentative_beliefs_global: list[ProfileBelief] = field(default_factory=list)
    tentative_beliefs_scoped: list[ProfileBelief] = field(default_factory=list)

    # ── derived signals (numeric) ───────────────────────────────────
    # Examples: latest_evidence_age_seconds, latest_episode_age_seconds.
    # Free-form per ADR — schema may evolve.
    signals: dict[str, float] = field(default_factory=dict)

    # ── counts ──────────────────────────────────────────────────────
    # Examples: total_observations, total_episodes, total_evidence,
    # active_beliefs, tentative_beliefs, stale_beliefs, invalidated_beliefs.
    counts: dict[str, int] = field(default_factory=dict)

    # ── freshness flags (booleans) ──────────────────────────────────
    # Examples: has_recent_observations, has_open_state, has_active_beliefs.
    # NOT decay — just operational freshness signals.
    freshness_flags: dict[str, bool] = field(default_factory=dict)

    # ── identity convenience ────────────────────────────────────────
    @property
    def current_observation_id(self) -> str | None:
        return self.current_observation.obs_id if self.current_observation else None

    @property
    def recent_observation_ids(self) -> list[str]:
        return [o.obs_id for o in self.recent_observations]

    @property
    def recent_episode_ids(self) -> list[str]:
        return [e.episode_id for e in self.recent_episodes]

    @property
    def retrieved_evidence_ids(self) -> list[str]:
        return [m.memory_id for m in self.retrieved_evidence]

    # ── belief access ───────────────────────────────────────────────
    #
    # The plain `active_beliefs` and `tentative_beliefs` properties
    # return the GLOBAL beliefs (context_key=None) for back-compat
    # with Block 5 callers. Scoped consumers should use the explicit
    # *_global / *_scoped fields directly.

    @property
    def active_beliefs(self) -> list[ProfileBelief]:
        """Active global beliefs only (Block 5 back-compat)."""
        return self.active_beliefs_global

    @property
    def tentative_beliefs(self) -> list[ProfileBelief]:
        """Tentative global beliefs only (Block 5 back-compat)."""
        return self.tentative_beliefs_global

    @property
    def active_belief_ids(self) -> list[str]:
        return [b.belief_id for b in self.active_beliefs_global]

    @property
    def tentative_belief_ids(self) -> list[str]:
        return [b.belief_id for b in self.tentative_beliefs_global]

    @property
    def all_active_beliefs(self) -> list[ProfileBelief]:
        """Active global + scoped beliefs combined.

        Useful for consumers that want the full picture without making
        the global/scoped distinction. Per guardrail B, callers that
        do this take responsibility for any reconciliation themselves —
        the runtime never reconciles them implicitly.
        """
        return self.active_beliefs_global + self.active_beliefs_scoped
