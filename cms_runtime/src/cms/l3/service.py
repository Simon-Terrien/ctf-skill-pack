"""
Evidence service — Block 3.

Responsibility (single):
    Run registered rules against observations and episodes.
    Persist one MemoryEvidence record per rule firing.
    Enforce idempotency: at most one record per
    (user_id, source_kind, source_id, rule_id).

Out of scope for this slice:
    - belief updates (Block 5)
    - contradiction tracking (Block 5)
    - retrieval ranking (Block 4)
    - decay (Block 5)
    - consolidation (Block 5)

Idempotency strategy
--------------------
The idempotency key is (user_id, source_kind, source_id, rule_id).

Primary layer (fast path): the store's has_evidence_for() check skips
filing if a record with the same key already exists.

Backstop (safety): the evidence_store also enforces UNIQUE at the schema
level on (user_id, source_kind, source_id, rule_id). If we ever race
or miss the fast-path check, the INSERT will fail rather than duplicate.

Scope enforcement
-----------------
The service validates that every payload.scope is in CANONICAL_SCOPES
before persisting. This is a non-negotiable policy boundary: rule authors
can experiment with new scopes, but they cannot leak into the store
without an explicit service-level update.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, Sequence

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l3.evidence import CANONICAL_SCOPES, MemoryEvidence
from cms.l3.rules import (
    DEFAULT_EPISODE_RULES,
    DEFAULT_OBSERVATION_RULES,
    EpisodeRule,
    EvidencePayload,
    ObservationRule,
)

if TYPE_CHECKING:
    from cms.storage.evidence_store import EvidenceStore


class EvidenceService:
    """File evidence from observations and episodes, enforcing idempotency."""

    def __init__(
        self,
        store: "EvidenceStore",
        *,
        observation_rules: Sequence[ObservationRule] | None = None,
        episode_rules: Sequence[EpisodeRule] | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        canonical_scopes: frozenset[str] = CANONICAL_SCOPES,
        supersession_window_days: int = 30,
    ):
        """
        Parameters
        ----------
        store
            Persistence backend.
        observation_rules, episode_rules
            Rule packs. Default to the full Block 3 canonical pack.
            Pass custom tuples to experiment without changing the defaults.
        clock
            Datetime factory. Injectable for tests.
        id_factory
            memory_id factory. Injectable for tests.
        canonical_scopes
            The set of scopes this service accepts from rules. Records
            whose payload.scope is outside this set raise ValueError at
            filing time. Block 3 default is the locked canonical set.
        supersession_window_days
            Block 6: when filing a new evidence record, any prior records
            in the same lane (user_id, rule_id, context_key) older than
            this many days are marked as superseded in the new record's
            `supersedes` list. Default 30 days. Set to 0 to disable.
        """
        self._store = store
        self._obs_rules = tuple(
            observation_rules if observation_rules is not None
            else DEFAULT_OBSERVATION_RULES
        )
        self._ep_rules = tuple(
            episode_rules if episode_rules is not None
            else DEFAULT_EPISODE_RULES
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._canonical_scopes = canonical_scopes
        self._supersession_window = timedelta(days=supersession_window_days)

    # ── public API ───────────────────────────────────────────────────

    def file_from_observation(
        self,
        obs: L1Observation,
        *,
        context_key: str | None = None,
    ) -> list[MemoryEvidence]:
        """Run observation-level rules against obs. Persist and return new records.

        Per Block 6: `context_key` is the caller-supplied belief-lane context.
        It propagates to each MemoryEvidence record produced from this
        observation. Does NOT affect evidence idempotency (guardrail A).
        """
        return self._file_rules(
            source_kind="observation",
            source_id=obs.obs_id,
            user_id=obs.user_id,
            rules=self._obs_rules,
            source=obs,
            context_key=context_key,
        )

    def file_from_episode(
        self,
        ep: L2Episode,
        *,
        context_key: str | None = None,
    ) -> list[MemoryEvidence]:
        """Run episode-level rules against ep. Persist and return new records."""
        return self._file_rules(
            source_kind="episode",
            source_id=ep.episode_id,
            user_id=ep.user_id,
            rules=self._ep_rules,
            source=ep,
            context_key=context_key,
        )

    # ── internals ────────────────────────────────────────────────────

    def _file_rules(
        self,
        *,
        source_kind: str,
        source_id: str,
        user_id: str,
        rules: Sequence,
        source,
        context_key: str | None = None,
    ) -> list[MemoryEvidence]:
        new_records: list[MemoryEvidence] = []
        now = self._clock()

        for rule in rules:
            payload = rule(source)
            if payload is None:
                continue

            # Scope policing — prevent rules from leaking new scopes
            if payload.scope not in self._canonical_scopes:
                raise ValueError(
                    f"Rule {payload.rule_id} produced non-canonical scope "
                    f"{payload.scope!r}. Allowed: {sorted(self._canonical_scopes)}"
                )

            # Idempotency fast path: skip if already filed.
            # Note: idempotency key does NOT include context_key (guardrail A).
            if self._store.has_evidence_for(
                user_id=user_id,
                source_kind=source_kind,
                source_id=source_id,
                rule_id=payload.rule_id,
            ):
                continue

            # Block 6: filing-time supersession.
            # Look up prior same-lane records older than the supersession
            # window. Lane = (user_id, rule_id, context_key). The new
            # record's `supersedes` field records the ids of what it
            # replaces. Old records stay in the store (audit history);
            # recompute will ignore them as primary support.
            supersedes_ids = self._find_lane_supersession(
                user_id=user_id,
                rule_id=payload.rule_id,
                context_key=context_key,
                now=now,
            )

            record = self._build_record(
                payload=payload,
                user_id=user_id,
                source_kind=source_kind,
                source_id=source_id,
                now=now,
                context_key=context_key,
                supersedes=supersedes_ids,
            )
            self._store.save(record)
            new_records.append(record)

        return new_records

    def _find_lane_supersession(
        self,
        *,
        user_id: str,
        rule_id: str,
        context_key: str | None,
        now: datetime,
    ) -> list[str]:
        """Find prior same-lane records that a new record should supersede.

        Lane-aware per the locked contract refinement: we key on
        (user_id, rule_id, context_key), NOT just (user_id, rule_id).
        Global and scoped lanes are distinct; supersession does not
        cross lanes.

        Window rule: a prior record is superseded if it is older than
        (now - supersession_window). If the window is zero, returns [].
        """
        if self._supersession_window.total_seconds() <= 0:
            return []
        cutoff = now - self._supersession_window
        prior = self._store.find_supersession_candidates(
            user_id=user_id,
            rule_id=rule_id,
            context_key=context_key,
        )
        return [r.memory_id for r in prior if r.created_at < cutoff]

    def _build_record(
        self,
        *,
        payload: EvidencePayload,
        user_id: str,
        source_kind: str,
        source_id: str,
        now: datetime | None = None,
        context_key: str | None = None,
        supersedes: list[str] | None = None,
    ) -> MemoryEvidence:
        return MemoryEvidence(
            memory_id=self._id_factory(),
            user_id=user_id,
            created_at=now if now is not None else self._clock(),
            source_kind=source_kind,  # type: ignore[arg-type]
            source_id=source_id,
            rule_id=payload.rule_id,
            scope=payload.scope,
            subscope=payload.subscope,
            tags=list(payload.tags),
            summary=payload.summary,
            support_score=payload.support_score,
            relevance_score=1.0,
            feature_snapshot=dict(payload.feature_snapshot),
            supersedes=list(supersedes) if supersedes else [],
            contradicted_by=[],
            context_key=context_key,
            metadata={},
        )
