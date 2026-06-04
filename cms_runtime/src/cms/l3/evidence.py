"""
L3A — Memory Evidence.

A MemoryEvidence record is an immutable, auditable statement of the form:

    "at time T, rule R fired on source object S, producing this summary."

Evidence is *not* belief. Evidence must never make identity claims about
the user ("user is X"). Summaries are deterministic, rule-owned, and
non-interpretive. Belief construction happens in Block 5, strictly on
top of persisted evidence.

Architectural rule (locked per ADR):
    "No long-term belief may exist without explicit supporting evidence
     references."

This dataclass is the concrete representation of the right-hand side of
that rule. Every future belief must reference memory_ids from this layer.

Provenance (mandatory)
----------------------
Every record carries:
    - source_kind: "observation" or "episode"
    - source_id:   the obs_id or episode_id that triggered the rule
    - rule_id:     the canonical rule identifier that produced it

These three together form the idempotency key. The evidence service
persists at most one record per (source_kind, source_id, rule_id) tuple,
so replay / retry / duplicate processing is safe.

Scope taxonomy (soft-canonical)
-------------------------------
The field types are free strings, but the evidence service restricts
producer behavior to a canonical set:

    scope ∈ {"pragmatic", "epistemic", "social", "dynamics"}
    subscope: rule-specific narrow label
        - certainty, hedging
        - self_reference, other_reference
        - rupture, sustained_regime
        - high_pragmatic_ratio, sustained_pragmatic_density

    tags: free labels like ["observation_level"] or ["episode_level",
          "surprise_closure"]

This keeps downstream retrieval and analytics consistent without
freezing a full ontology before real patterns emerge.

Contradiction fields (Block 5)
-------------------------------
supersedes and contradicted_by are present, persisted, and list-typed
from day one — but never populated by Block 3. Block 5 is the only layer
allowed to write them. Keeping the fields inert-but-present avoids a
structural migration later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

SourceKind = Literal["observation", "episode"]

# Canonical scopes — policed by the evidence service, not by the dataclass
# (so research can experiment with new scopes behind the service interface
# without breaking stored records).
CANONICAL_SCOPES: frozenset[str] = frozenset({
    "pragmatic",
    "epistemic",
    "social",
    "dynamics",
})


@dataclass(slots=True)
class MemoryEvidence:
    """Immutable record of one rule firing on one source object."""

    # ── identity ────────────────────────────────────────────────────
    memory_id: str
    user_id: str
    created_at: datetime

    # ── provenance (mandatory per the contract) ─────────────────────
    source_kind: SourceKind
    source_id: str
    rule_id: str

    # ── scope (soft-canonical) ──────────────────────────────────────
    scope: str              # see CANONICAL_SCOPES
    subscope: str | None    # rule-specific narrow label
    tags: list[str] = field(default_factory=list)

    # ── content ─────────────────────────────────────────────────────
    # Deterministic, rule-owned, non-interpretive. The summary MUST NOT
    # make identity claims ("user is X"). See CMSEngine tests for
    # enforcement of this boundary.
    summary: str = ""

    # ── scoring (computed at filing time) ───────────────────────────
    # Bounded, deterministic, local to the rule firing. No global context
    # at this layer — retrieval in Block 4 may reinterpret relevance.
    support_score: float = 0.0    # how strongly the rule was triggered
    relevance_score: float = 1.0  # default 1.0 at creation

    # ── feature snapshot ────────────────────────────────────────────
    # Optional: the small set of feature values the rule consulted.
    # Kept bounded (dict[str, float]) so it stays auditable and cheap.
    feature_snapshot: dict[str, float] = field(default_factory=dict)

    # ── contradiction fields (Block 5 — inert in Block 3) ───────────
    # List-typed from day one to avoid structural migration later.
    supersedes: list[str] = field(default_factory=list)
    contradicted_by: list[str] = field(default_factory=list)

    # ── context key (Block 6) ───────────────────────────────────────
    # Caller-supplied belief-lane context. None means "global" — the
    # caller did not scope this turn. Non-None routes belief updates
    # into a scoped lane keyed on (user_id, dimension, context_key).
    #
    # GUARDRAIL A (locked): context_key does NOT enter the evidence
    # idempotency key. Replaying the same source object with a
    # different context_key must not create a duplicate record;
    # idempotency stays grounded in source/rule identity.
    context_key: str | None = None

    # ── extension ───────────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── invariants ──────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if not self.memory_id:
            raise ValueError("memory_id is required")
        if not self.user_id:
            raise ValueError("user_id is required")
        if self.source_kind not in ("observation", "episode"):
            raise ValueError(
                f"source_kind must be 'observation' or 'episode', "
                f"got {self.source_kind!r}"
            )
        if not self.source_id:
            raise ValueError("source_id is required")
        if not self.rule_id:
            raise ValueError("rule_id is required")
        if not self.scope:
            raise ValueError("scope is required")
        # support_score must be in [0, 1] — it's a strength signal, not a logit
        if not (0.0 <= self.support_score <= 1.0):
            raise ValueError(
                f"support_score must be in [0, 1], got {self.support_score}"
            )
        if not (0.0 <= self.relevance_score <= 1.0):
            raise ValueError(
                f"relevance_score must be in [0, 1], got {self.relevance_score}"
            )

    # ── idempotency key ─────────────────────────────────────────────

    @property
    def idempotency_key(self) -> tuple[str, str, str, str]:
        """Tuple that uniquely identifies this rule firing.

        Includes user_id for safety — different users may coincidentally
        share source_ids in some edge case (migration scenarios), so we
        always scope idempotency to the user.
        """
        return (self.user_id, self.source_kind, self.source_id, self.rule_id)
