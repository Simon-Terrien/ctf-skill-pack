"""
Retrieval service — Block 4.

Responsibilities (single):
    Given a (user, session) and optional scope/subscope filters,
    return the recent observations, recent episodes, and ranked
    evidence candidates needed to assemble a RuntimeStateView.

Out of scope:
    - belief inference (Block 5)
    - contradiction handling (Block 5)
    - decay-based scoring (Block 5)
    - LLM/agent/dashboard formatting
    - learned ranking
    - diversity-aware selection (deferred per "cherry on top")

Deterministic ordering for evidence (locked Block 4 contract)
-------------------------------------------------------------
Per the Block 4 spec, evidence ranking is:

    1. pinned=True first (Block 5 pinning is not implemented yet —
       this is enforced by sorting on a derived flag, currently always
       False; the slot is reserved so the policy holds its shape)
    2. exact scope match before broader results
    3. exact subscope match before broader results
    4. newer records before older records
    5. higher support_score before lower
    6. memory_id DESC for deterministic tie-break

The store provides candidates already ordered by (created_at DESC,
support_score DESC, memory_id DESC). RetrievalService re-sorts to
apply the full policy, including scope-exactness preference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l3.evidence import MemoryEvidence
from cms.runtime.state import RetrievalPolicy

if TYPE_CHECKING:
    from cms.storage.episode_store import EpisodeStore
    from cms.storage.evidence_store import EvidenceStore
    from cms.storage.observation_store import ObservationStore


class RetrievalService:
    """Compose store queries into ranked candidate lists for state assembly."""

    def __init__(
        self,
        observation_store: "ObservationStore",
        episode_store: "EpisodeStore",
        evidence_store: "EvidenceStore",
        *,
        policy: RetrievalPolicy | None = None,
    ):
        self._obs_store = observation_store
        self._ep_store = episode_store
        self._ev_store = evidence_store
        self._policy = policy or RetrievalPolicy()

    @property
    def policy(self) -> RetrievalPolicy:
        return self._policy

    # ── observations ────────────────────────────────────────────────

    def get_recent_observations(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> list[L1Observation]:
        """Most recent observations for the session, newest first.

        Defaults to policy.observation_limit. Returns an empty list if
        none exist.
        """
        n = limit if limit is not None else self._policy.observation_limit
        if n == 0:
            return []
        return self._obs_store.latest_for_session(user_id, session_id, limit=n)

    # ── episodes ────────────────────────────────────────────────────

    def get_recent_episodes(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> list[L2Episode]:
        """Most recent closed episodes for the session, newest first.

        Open episodes (in EpisodeService memory) are not visible here —
        only persisted ones are returned.
        """
        n = limit if limit is not None else self._policy.episode_limit
        if n == 0:
            return []
        return self._ep_store.latest_for_session(user_id, session_id, limit=n)

    # ── evidence ────────────────────────────────────────────────────

    def search_evidence(
        self,
        user_id: str,
        *,
        scope: str | None = None,
        subscope: str | None = None,
        source_kind: str | None = None,
        limit: int | None = None,
    ) -> list[MemoryEvidence]:
        """Ranked evidence candidates per the Block 4 ordering policy.

        Filtering is service-level: when scope is provided, exact-scope
        records rank above broader records. Same for subscope. To
        retrieve only exact matches, pass the filter and treat the
        result as authoritative.
        """
        n = limit if limit is not None else self._policy.evidence_limit
        if n == 0:
            return []

        # Pull a generous candidate pool — we re-rank in service layer.
        # 4× the desired limit gives us room to apply the ordering policy
        # without missing candidates that scope-exactness would promote.
        pool_size = max(n * 4, 20)

        # When scope or subscope is given, the store's WHERE clause is
        # already exact-match — no broader results would appear, so the
        # exactness step in the ranking is a no-op. The mechanism still
        # holds for future "broaden if no exact matches" variants.
        candidates = self._ev_store.search(
            user_id=user_id,
            scope=scope,
            subscope=subscope,
            source_kind=source_kind,
            limit=pool_size,
        )

        ranked = self._rank_evidence(
            candidates,
            requested_scope=scope,
            requested_subscope=subscope,
        )
        return ranked[:n]

    # ── ordering policy ─────────────────────────────────────────────

    @staticmethod
    def _rank_evidence(
        candidates: list[MemoryEvidence],
        *,
        requested_scope: str | None,
        requested_subscope: str | None,
    ) -> list[MemoryEvidence]:
        """Apply the locked Block 4 ordering policy.

        Sort key (smaller = ranks higher in ascending sort):
            1. -pinned        (True first)
            2. -scope_exact   (exact match first when scope was requested)
            3. -subscope_exact (exact match first when subscope was requested)
            4. -created_epoch (newer first)
            5. -support_score (stronger first)
            6. -memory_id     (deterministic tie-break, DESC for consistency
                               with store ordering)
        """
        def sort_key(m: MemoryEvidence) -> tuple:
            # Pinned: not implemented in Block 3 — slot reserved for Block 5
            pinned = bool(m.metadata.get("pinned", False))
            scope_exact = (
                requested_scope is not None and m.scope == requested_scope
            )
            subscope_exact = (
                requested_subscope is not None
                and m.subscope == requested_subscope
            )
            # Use negative for DESC behavior under default ascending sort
            return (
                not pinned,           # pinned True → False sorts first
                not scope_exact,      # exact True → False sorts first
                not subscope_exact,   # exact True → False sorts first
                -m.created_at.timestamp(),
                -m.support_score,
                # memory_id DESC: invert lexicographic comparison via
                # a key that flips ordering when sorted ascending.
                # Tuple of (-len, reverse-codepoints) handles arbitrary strings.
                tuple(-ord(c) for c in m.memory_id),
            )

        return sorted(candidates, key=sort_key)
