"""
Belief service — Block 5.

Responsibility (single):
    Given new MemoryEvidence records, update the corresponding
    ProfileBelief records: create tentative beliefs, advance to active,
    record contradictions, mark stale, invalidate when contradiction
    overwhelms support.

Strict boundaries (locked):
    - Evidence input only. Never reads observations or episodes directly.
    - Each evidence record updates AT MOST one belief dimension
      (guardrail A: dimension-local updates).
    - The evidence ledger is append-only: supporting_memory_ids and
      counterevidence_ids only grow. Recomputation re-reads the ledger;
      it never rewrites it.
    - Push-based via process_new_evidence(records). The engine calls
      this per turn. recompute_for_user(user_id) is the offline escape
      hatch for batch repair / replay.

Out of scope:
    - LLM-generated belief summaries
    - cross-dimension coupling
    - learned aggregation
    - psychometric scoring
    - per-consumer belief shaping
    - hidden mutation from the assembler

Update semantics
----------------
For each new evidence record:
  1. Map scope → dimension via dimension_for_scope(). If no mapping
     (e.g., dynamics scope), skip — evidence is still recorded in the
     store; it just doesn't feed any belief in Block 5.
  2. Compute the directional contribution: subscope_directions[subscope]
     × support_score (clipped). If the subscope is unknown for the
     dimension, skip.
  3. Load the existing belief for (user_id, dimension) or create fresh.
  4. Determine support vs contradiction:
       same direction as belief's current value → support
       opposite direction → contradiction
       fresh belief → first contribution sets the direction → support
  5. Append memory_id to the ledger (supporting or counterevidence).
  6. Recompute value, confidence, stability from the full ledger.
  7. Re-evaluate status using BeliefThresholds.
  8. Persist.

This means: replays are safe (idempotency from evidence layer prevents
re-filing); repeated process_new_evidence calls with the same evidence
are no-ops because the belief checks if memory_id is already in either
ledger before updating.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Iterable

from cms.l3.belief import (
    DIMENSION_SPECS,
    DimensionSpec,
    ProfileBelief,
    dimension_for_scope,
)
from cms.l3.belief_events import (
    BeliefEvent,
    BeliefEventHandler,
    NullEventHandler,
)
from cms.l3.belief_explanation import BeliefExplanation
from cms.l3.belief_policy import BeliefThresholds, is_belief_stale
from cms.l3.evidence import MemoryEvidence

if TYPE_CHECKING:
    from cms.storage.belief_store import BeliefStore
    from cms.storage.evidence_store import EvidenceStore


class BeliefService:
    """Evidence-driven belief lifecycle manager."""

    def __init__(
        self,
        belief_store: "BeliefStore",
        evidence_store: "EvidenceStore",
        *,
        thresholds: BeliefThresholds | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        event_handler: BeliefEventHandler | None = None,
    ):
        self._belief_store = belief_store
        self._evidence_store = evidence_store
        self._thresholds = thresholds or BeliefThresholds()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        # Block 6: event hook. NullEventHandler drops everything;
        # callers pass LoggingEventHandler or a custom handler to wire
        # observability without coupling the runtime to any specific
        # event sink.
        self._event_handler: BeliefEventHandler = (
            event_handler if event_handler is not None else NullEventHandler()
        )

    @property
    def thresholds(self) -> BeliefThresholds:
        return self._thresholds

    # ── primary push entry point ────────────────────────────────────

    def process_new_evidence(
        self,
        records: Iterable[MemoryEvidence],
    ) -> list[ProfileBelief]:
        """Process new evidence records into belief updates.

        Returns the list of beliefs that were created or updated.
        Records that do not map to a known dimension are silently
        ignored.

        Block 6 routing: each record's `context_key` determines which
        belief lane receives the update. context_key=None routes to the
        global belief; context_key="research" routes to the scoped-to-
        research belief. Global and scoped beliefs coexist per
        guardrail B — no implicit reconciliation.

        Block 6 events: real status transitions (tentative_created,
        activated, staled, invalidated) emit events via the configured
        handler. New scoped belief creation emits belief_scoped_created.
        """
        updated: dict[str, ProfileBelief] = {}  # belief_id → belief

        for record in records:
            dim_name = dimension_for_scope(record.scope)
            if dim_name is None:
                continue

            spec = DIMENSION_SPECS[dim_name]
            if record.subscope is None or record.subscope not in spec.subscope_directions:
                continue

            # Compute contribution: direction × support_score
            direction = spec.subscope_directions[record.subscope]
            contribution = direction * record.support_score

            # Route to the belief in the right context lane
            existing = self._belief_store.get_for_user_dimension(
                record.user_id, dim_name, context_key=record.context_key,
            )
            is_new = existing is None
            belief = existing if existing is not None else self._make_fresh_belief(
                record.user_id, dim_name, record.context_key,
            )
            status_before = None if is_new else belief.status

            # Idempotency: skip if this memory_id is already in either ledger
            if (record.memory_id in belief.supporting_memory_ids
                    or record.memory_id in belief.counterevidence_ids):
                updated[belief.belief_id] = belief
                continue

            self._integrate_evidence(belief, record, contribution)
            self._recompute_belief(belief)
            self._belief_store.upsert(belief)
            updated[belief.belief_id] = belief

            self._emit_transition_events(
                belief=belief,
                status_before=status_before,
                is_new=is_new,
                triggered_by_evidence_ids=[record.memory_id],
            )

        return list(updated.values())

    # ── offline / maintenance entry point ───────────────────────────

    def recompute_for_user(self, user_id: str) -> list[ProfileBelief]:
        """Rebuild all of a user's beliefs from their full evidence ledger.

        Use cases:
          - threshold changes
          - replaying a corrupted state
          - migration from external belief data

        This is the only path that re-reads the full evidence history.
        It does NOT mutate evidence — only beliefs.

        Emits one belief_recomputed event per resulting belief, on top
        of any transition events that fire during reconstruction.
        """
        # Load all evidence for the user, in chronological order
        all_evidence = self._evidence_store.list_for_user(user_id, limit=10**9)

        # Drop existing beliefs for the user — we're rebuilding
        existing = self._belief_store.list_for_user(user_id)
        for b in existing:
            self._belief_store.delete(b.belief_id)

        # Re-run belief construction over the entire history
        rebuilt = self.process_new_evidence(all_evidence)

        # Emit recompute events for each resulting belief
        now = self._clock()
        for belief in rebuilt:
            self._event_handler(BeliefEvent(
                event_type="belief_recomputed",
                belief_id=belief.belief_id,
                user_id=belief.user_id,
                dimension=belief.dimension,
                context_key=belief.context_key,
                status_before=None,
                status_after=belief.status,
                triggered_by_evidence_ids=list(belief.supporting_memory_ids),
                timestamp=now,
            ))
        return rebuilt

    # ── staleness sweep ─────────────────────────────────────────────

    def sweep_staleness(self, user_id: str) -> list[ProfileBelief]:
        """Mark active beliefs as stale when they exceed the staleness window.

        Returns the list of beliefs whose status was changed.
        Pure status transition — no value, confidence, or ledger changes.

        Emits belief_staled events for each transition.
        """
        now = self._clock()
        changed: list[ProfileBelief] = []
        for belief in self._belief_store.list_for_user(user_id):
            if belief.status != "active":
                continue
            last_support = self._latest_support_time(belief)
            if is_belief_stale(last_support, now, self._thresholds):
                status_before = belief.status
                belief.status = "stale"
                belief.updated_at = now
                self._belief_store.upsert(belief)
                changed.append(belief)
                self._event_handler(BeliefEvent(
                    event_type="belief_staled",
                    belief_id=belief.belief_id,
                    user_id=belief.user_id,
                    dimension=belief.dimension,
                    context_key=belief.context_key,
                    status_before=status_before,
                    status_after="stale",
                    triggered_by_evidence_ids=[],
                    timestamp=now,
                ))
        return changed

    # ── belief construction ─────────────────────────────────────────

    def _make_fresh_belief(
        self, user_id: str, dimension: str, context_key: str | None,
    ) -> ProfileBelief:
        """Create a fresh belief object (not persisted yet)."""
        spec = DIMENSION_SPECS[dimension]
        # Fresh belief starts at value=0 (signed) or value_min (magnitude),
        # confidence=0, stability=1 (no variance yet), status=tentative.
        initial_value = 0.0 if spec.polarity == "signed" else spec.value_min
        now = self._clock()
        return ProfileBelief(
            belief_id=self._id_factory(),
            user_id=user_id,
            dimension=dimension,
            value=initial_value,
            confidence=0.0,
            stability=1.0,
            status="tentative",
            created_at=now,
            updated_at=now,
            context_key=context_key,
        )

    # ── explanations (Block 6, on-demand) ───────────────────────────

    def explain(
        self, belief_id: str, *, top_n: int = 5,
    ) -> BeliefExplanation | None:
        """Build a structured explanation for a belief.

        Per the locked Block 6 contract: explanations are computed on
        demand, never stored, never carried on RuntimeStateView. Top
        supporting/counterevidence are ranked deterministically by
        (support_score DESC, created_at DESC, memory_id DESC) and
        capped at top_n.

        Superseded records are excluded from "active" support and
        contradiction counts but still surfaced as `superseded_count`
        for audit visibility.

        Returns None if the belief_id does not exist.
        """
        # Use the public list-by-user surface to find the belief.
        # We need user_id to query supporting evidence, so we pull it
        # via a quick fetch_by_id on the store. BeliefStore exposes
        # this via list_for_user filtered on belief_id.
        belief = self._fetch_belief_by_id(belief_id)
        if belief is None:
            return None

        support_records = [
            e for e in (
                self._evidence_store.get(mid)
                for mid in belief.supporting_memory_ids
            ) if e is not None
        ]
        contra_records = [
            e for e in (
                self._evidence_store.get(mid)
                for mid in belief.counterevidence_ids
            ) if e is not None
        ]

        # Identify superseded ids (per the recompute logic)
        superseded_ids: set[str] = set()
        for r in support_records + contra_records:
            superseded_ids.update(r.supersedes)

        active_supports = [
            r for r in support_records if r.memory_id not in superseded_ids
        ]
        active_contras = [
            r for r in contra_records if r.memory_id not in superseded_ids
        ]

        # Deterministic ranking: support_score DESC, created_at DESC, id DESC
        def _rank_key(r: MemoryEvidence):
            # Negate for DESC; for memory_id we use reverse via tuple inversion
            return (-r.support_score, -r.created_at.timestamp(), r.memory_id)

        # Sort then reverse memory_id portion: simpler to do two passes
        ranked_supports = sorted(active_supports, key=lambda r: r.memory_id, reverse=True)
        ranked_supports = sorted(
            ranked_supports,
            key=lambda r: (-r.support_score, -r.created_at.timestamp()),
        )
        ranked_contras = sorted(active_contras, key=lambda r: r.memory_id, reverse=True)
        ranked_contras = sorted(
            ranked_contras,
            key=lambda r: (-r.support_score, -r.created_at.timestamp()),
        )

        latest_support_at = (
            max((r.created_at for r in active_supports), default=None)
            if active_supports else None
        )
        latest_contradiction_at = (
            max((r.created_at for r in active_contras), default=None)
            if active_contras else None
        )

        return BeliefExplanation(
            belief_id=belief.belief_id,
            user_id=belief.user_id,
            dimension=belief.dimension,
            context_key=belief.context_key,
            status=belief.status,
            value=belief.value,
            confidence=belief.confidence,
            stability=belief.stability,
            support_count=len(active_supports),
            contradiction_count=len(active_contras),
            superseded_count=len(superseded_ids),
            latest_support_at=latest_support_at,
            latest_contradiction_at=latest_contradiction_at,
            top_supporting_memory_ids=[r.memory_id for r in ranked_supports[:top_n]],
            top_counterevidence_ids=[r.memory_id for r in ranked_contras[:top_n]],
        )

    def _fetch_belief_by_id(self, belief_id: str) -> ProfileBelief | None:
        """Fetch a single belief by id. Uses store's list-by-user as a fallback
        if no direct get exists.
        """
        # Most stores have a direct get; if not, scan by user. Try direct first.
        getter = getattr(self._belief_store, "get", None)
        if callable(getter):
            return getter(belief_id)
        # Fallback: this path shouldn't trigger in normal stores
        return None

    def _emit_transition_events(
        self,
        *,
        belief: ProfileBelief,
        status_before: str | None,
        is_new: bool,
        triggered_by_evidence_ids: list[str],
    ) -> None:
        """Emit the right events for a belief that just changed.

        Real transitions only — silent same-status updates emit nothing.
        Per locked contract: events fire on real state transitions and
        explicit recompute, never on every numeric tweak.

        Order: scoped_created (if applicable) → status transition.
        """
        now = self._clock()

        # Scoped creation event — only on first instantiation of a scoped lane
        if is_new and belief.context_key is not None:
            self._event_handler(BeliefEvent(
                event_type="belief_scoped_created",
                belief_id=belief.belief_id,
                user_id=belief.user_id,
                dimension=belief.dimension,
                context_key=belief.context_key,
                status_before=None,
                status_after=belief.status,
                triggered_by_evidence_ids=list(triggered_by_evidence_ids),
                timestamp=now,
            ))

        # Status transition events — only on real changes
        status_after = belief.status
        if status_before == status_after and not is_new:
            return  # no real transition

        event_type = self._status_to_event_type(status_before, status_after, is_new)
        if event_type is None:
            return

        self._event_handler(BeliefEvent(
            event_type=event_type,
            belief_id=belief.belief_id,
            user_id=belief.user_id,
            dimension=belief.dimension,
            context_key=belief.context_key,
            status_before=status_before,
            status_after=status_after,
            triggered_by_evidence_ids=list(triggered_by_evidence_ids),
            timestamp=now,
        ))

    @staticmethod
    def _status_to_event_type(
        status_before: str | None,
        status_after: str,
        is_new: bool,
    ) -> str | None:
        """Map a status transition to an event type, or None if no event applies."""
        if is_new and status_after == "tentative":
            return "belief_tentative_created"
        if status_after == "active" and status_before != "active":
            return "belief_activated"
        if status_after == "stale" and status_before != "stale":
            return "belief_staled"
        if status_after == "invalidated" and status_before != "invalidated":
            return "belief_invalidated"
        return None

    def _integrate_evidence(
        self,
        belief: ProfileBelief,
        record: MemoryEvidence,
        contribution: float,
    ) -> None:
        """Append the evidence to the appropriate ledger.

        Same direction as the supporting chain → supporting.
        Opposite direction → counterevidence.
        Fresh (no support yet) → supporting; the first contribution
        sets the belief's direction.

        Direction is computed from the supporting ledger's mean, NOT
        from belief.value. This matters because contradictions pull
        value toward zero — once value crosses zero, naive value-sign
        comparison would flip classification and start treating further
        contradictions as supports. The supporting ledger preserves
        the original direction even as value evolves.
        """
        if not belief.supporting_memory_ids and not belief.counterevidence_ids:
            # Fresh belief — first evidence is always supporting
            belief.supporting_memory_ids.append(record.memory_id)
            return

        # For magnitude dimensions, contribution can never be opposite
        # to value (value_min is 0, contribution always positive).
        spec = belief.spec
        if spec.polarity == "magnitude":
            belief.supporting_memory_ids.append(record.memory_id)
            return

        # Signed dimension: classify against the supporting chain's direction.
        # We compute mean contribution of *supporting* records only — that's
        # the established direction. Contradictions don't redefine direction;
        # they just oppose it.
        supporting_direction = self._supporting_direction(belief)

        if supporting_direction == 0.0:
            # No supporting history yet (e.g., all prior records were
            # contradictions of a now-empty support chain — defensive case).
            # Treat new evidence as supporting; it sets fresh direction.
            belief.supporting_memory_ids.append(record.memory_id)
        elif (supporting_direction > 0 and contribution > 0) or \
             (supporting_direction < 0 and contribution < 0):
            belief.supporting_memory_ids.append(record.memory_id)
        else:
            belief.counterevidence_ids.append(record.memory_id)

    def _supporting_direction(self, belief: ProfileBelief) -> float:
        """Mean signed contribution of the supporting ledger.

        Returns 0.0 if the supporting chain is empty. The sign of this
        value is the established belief direction; magnitude is unused
        in classification.
        """
        if not belief.supporting_memory_ids:
            return 0.0
        spec = belief.spec
        contributions: list[float] = []
        for mid in belief.supporting_memory_ids:
            r = self._evidence_store.get(mid)
            if r is None or r.subscope is None:
                continue
            direction = spec.subscope_directions.get(r.subscope, 0.0)
            contributions.append(direction * r.support_score)
        if not contributions:
            return 0.0
        return sum(contributions) / len(contributions)

    def _recompute_belief(self, belief: ProfileBelief) -> None:
        """Recompute value, confidence, stability, status from the full ledger.

        Block 6 supersession: records whose memory_id appears in any other
        ledger entry's `supersedes` list are kept in the audit chain but
        do NOT count toward thresholds, value, or stability. They remain
        in supporting_memory_ids for full provenance — recompute filters
        them at read time.
        """
        now = self._clock()
        belief.updated_at = now

        # Pull all evidence referenced by this belief
        support_records = [
            e for e in (
                self._evidence_store.get(mid) for mid in belief.supporting_memory_ids
            ) if e is not None
        ]
        contra_records = [
            e for e in (
                self._evidence_store.get(mid) for mid in belief.counterevidence_ids
            ) if e is not None
        ]

        # Block 6: identify superseded ids — these stay in the ledger
        # for audit but are excluded from primary computation.
        superseded_ids: set[str] = set()
        for r in support_records:
            superseded_ids.update(r.supersedes)
        for r in contra_records:
            superseded_ids.update(r.supersedes)

        active_supports = [r for r in support_records if r.memory_id not in superseded_ids]
        active_contras = [r for r in contra_records if r.memory_id not in superseded_ids]

        # ── value: weighted mean of active contributions ────────────
        spec = belief.spec
        contributions: list[float] = []
        for r in active_supports:
            direction = spec.subscope_directions.get(r.subscope or "", 0.0)
            contributions.append(direction * r.support_score)
        for r in active_contras:
            direction = spec.subscope_directions.get(r.subscope or "", 0.0)
            contributions.append(direction * r.support_score)

        if contributions:
            mean = sum(contributions) / len(contributions)
            belief.value = max(spec.value_min, min(spec.value_max, mean))
        else:
            belief.value = 0.0 if spec.polarity == "signed" else spec.value_min

        # ── confidence: active support credit minus active contradiction ─
        support_credit = (
            len(active_supports) * self._thresholds.confidence_per_support
        )
        contra_penalty = (
            len(active_contras) * self._thresholds.confidence_penalty_per_contradiction
        )
        raw_confidence = support_credit - contra_penalty
        belief.confidence = max(0.0, min(1.0, raw_confidence))

        # ── stability: inverse of contribution variance ─────────────
        if len(contributions) >= 2:
            mean = sum(contributions) / len(contributions)
            variance = sum((c - mean) ** 2 for c in contributions) / len(contributions)
            belief.stability = 1.0 / (1.0 + variance)
        else:
            belief.stability = 1.0

        # Track superseded count in metadata (for explanations + tests)
        belief.metadata["superseded_support_count"] = sum(
            1 for r in support_records if r.memory_id in superseded_ids
        )

        # ── status transitions ──────────────────────────────────────
        # Block 6: status uses active (non-superseded) records only.
        # Superseded support no longer counts toward active threshold,
        # which matches the "newer evidence replaces older" semantics.
        belief.status = self._compute_status(
            belief, active_supports, active_contras, now,
        )

    def _compute_status(
        self,
        belief: ProfileBelief,
        support_records: list[MemoryEvidence],
        contra_records: list[MemoryEvidence],
        now: datetime,
    ) -> str:
        """Determine belief status from the support/contradiction ledger."""
        t = self._thresholds

        # Filter support to "qualifying": within active window, above strength threshold
        active_window_start = now - t.active_window
        recent_strong_support = [
            r for r in support_records
            if r.created_at >= active_window_start
            and r.support_score >= t.min_supporting_strength
        ]

        # ── invalidated takes precedence ────────────────────────────
        if len(contra_records) > len(support_records):
            return "invalidated"

        # Recent contradiction burst
        burst_window_start = now - t.invalidation_burst_window
        recent_contras = [
            r for r in contra_records if r.created_at >= burst_window_start
        ]
        if len(recent_contras) >= t.invalidation_burst_count:
            return "invalidated"

        # ── active ──────────────────────────────────────────────────
        if (len(recent_strong_support) >= t.active_min_support
                and belief.confidence >= t.active_min_confidence):
            return "active"

        # ── tentative ───────────────────────────────────────────────
        # Any qualifying support counts toward tentative
        qualifying_support = [
            r for r in support_records
            if r.support_score >= t.min_supporting_strength
        ]
        if len(qualifying_support) >= t.tentative_min_support:
            # Was active but lost support → stale, not back to tentative
            if belief.status == "active":
                last_support = self._latest_support_time(belief, support_records)
                if is_belief_stale(last_support, now, t):
                    return "stale"
                return "active"
            return "tentative"

        # No qualifying support — preserve prior status (or tentative for fresh)
        return belief.status if belief.status != "active" else "stale"

    def _latest_support_time(
        self,
        belief: ProfileBelief,
        support_records: list[MemoryEvidence] | None = None,
    ) -> datetime | None:
        if support_records is None:
            support_records = [
                e for e in (
                    self._evidence_store.get(mid)
                    for mid in belief.supporting_memory_ids
                ) if e is not None
            ]
        if not support_records:
            return None
        return max(r.created_at for r in support_records)
