"""
L3B — Profile Belief.

A ProfileBelief is the first interpretive layer above evidence:

    "this support chain over evidence records suggests an
     epistemic_style value of +0.6 with confidence 0.7."

Beliefs are NOT identity claims. Belief summaries describe patterns
in observed behavior, not who the user "is."

Boundary (locked per Block 5 contract):
  - Beliefs derive only from evidence — never directly from raw
    observations or episodes.
  - Each evidence record updates AT MOST one belief dimension
    (dimension-local updates per guardrail A).
  - StateAssembler reads beliefs but never mutates them (guardrail B).

Per-dimension contracts
-----------------------
Block 5 ships three dimensions, each with explicit value semantics.
The DIMENSION_SPECS registry pins the contract:

  - epistemic_style    : value ∈ [-1, +1]   (-1 hedging, +1 certainty)
  - social_orientation : value ∈ [-1, +1]   (-1 self-ref, +1 other-ref)
  - pragmatic_style    : value ∈ [ 0, +1]   ( 0 none, +1 sustained density)

confidence and stability are always [0, 1].

Status lifecycle
----------------
  tentative   : first qualifying support chain (1-2 same-direction evidence)
  active      : ≥3 supporting evidence within active_window_days,
                confidence ≥ active_confidence_min
  stale       : active belief with no supporting evidence in stale_window_days
  invalidated : contradiction overwhelms support, or recent contradiction burst

Block 5 vocabulary note
-----------------------
We use "staleness" only — never "decay". Decay implies continuous score
mutation. Block 5 does discrete status transitions over a ledger of
evidence references. The ledger is never rewritten; only the belief's
status, value, and confidence change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from cms.l3.evidence import CANONICAL_SCOPES

BeliefStatus = Literal["tentative", "active", "stale", "invalidated"]

VALID_BELIEF_STATUSES: frozenset[str] = frozenset({
    "tentative", "active", "stale", "invalidated",
})


# ── per-dimension contracts ──────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class DimensionSpec:
    """Per-dimension value semantics — pinned contract.

    Avoids the trap of pretending all dimensions share semantics just
    because they share a 'value' field name.
    """
    name: str
    value_min: float
    value_max: float
    polarity: Literal["signed", "magnitude"]
    # Which evidence scope feeds this dimension. Block 5 uses strict
    # scope-to-dimension mapping. Block 6+ may add cross-scope dimensions.
    source_scope: str
    # How each subscope contributes a directional signal. Positive
    # contributions push value toward value_max; negative toward value_min.
    # For magnitude dimensions, only positive contributions are meaningful.
    subscope_directions: dict[str, float]


# Block 5 + Block 6 dimension registry.
#
# Block 5 dimensions were scope-pure: one dimension per evidence scope.
# Block 6 adds `interaction_stability`, which consumes the `dynamics`
# scope — the first cross-scope dimension. It reads only one scope,
# but demonstrates the pattern: the mapping is explicit via source_scope,
# not inferred from dimension name or fuzzy coupling.
#
# Future dimensions MAY consume multiple scopes. When that happens,
# source_scope becomes a set rather than a single string, and the
# service must route evidence to the right dimension by explicit
# registry lookup — never free-form "this might be relevant" inference.
DIMENSION_SPECS: dict[str, DimensionSpec] = {
    "epistemic_style": DimensionSpec(
        name="epistemic_style",
        value_min=-1.0,
        value_max=+1.0,
        polarity="signed",
        source_scope="epistemic",
        subscope_directions={
            "certainty": +1.0,   # supports +value
            "hedging":  -1.0,    # supports -value
        },
    ),
    "social_orientation": DimensionSpec(
        name="social_orientation",
        value_min=-1.0,
        value_max=+1.0,
        polarity="signed",
        source_scope="social",
        subscope_directions={
            "self_reference":  -1.0,
            "other_reference": +1.0,
        },
    ),
    "pragmatic_style": DimensionSpec(
        name="pragmatic_style",
        value_min=0.0,
        value_max=+1.0,
        polarity="magnitude",
        source_scope="pragmatic",
        subscope_directions={
            "high_pragmatic_ratio":         +1.0,
            "sustained_pragmatic_density":  +1.0,
        },
    ),
    # ── Block 6 addition ──────────────────────────────────────────
    # interaction_stability reads `dynamics` scope evidence.
    # Signed dimension: -1 = highly rupture-prone / unstable pattern,
    # +1 = sustained regime / stable interaction pattern.
    #
    # `rupture` evidence pulls toward instability (-1 direction).
    # `sustained_regime` evidence pulls toward stability (+1 direction).
    "interaction_stability": DimensionSpec(
        name="interaction_stability",
        value_min=-1.0,
        value_max=+1.0,
        polarity="signed",
        source_scope="dynamics",
        subscope_directions={
            "rupture":           -1.0,
            "sustained_regime":  +1.0,
        },
    ),
}


def dimension_for_scope(scope: str) -> str | None:
    """Return the belief dimension fed by `scope`, or None if no mapping.

    Block 5 strict mapping: dynamics scope feeds no dimension.
    """
    for name, spec in DIMENSION_SPECS.items():
        if spec.source_scope == scope:
            return name
    return None


# Validate at import: every spec's source_scope must be canonical.
for _spec in DIMENSION_SPECS.values():
    if _spec.source_scope not in CANONICAL_SCOPES:
        raise RuntimeError(
            f"DimensionSpec {_spec.name!r} references non-canonical scope "
            f"{_spec.source_scope!r}"
        )


# ── ProfileBelief dataclass ──────────────────────────────────────────


@dataclass(slots=True)
class ProfileBelief:
    """Persistent, evidence-backed interpretive record."""

    # ── identity ────────────────────────────────────────────────────
    belief_id: str
    user_id: str
    dimension: str  # must be a key in DIMENSION_SPECS

    # ── interpretive content ────────────────────────────────────────
    # value semantics depend on the dimension — see DIMENSION_SPECS
    value: float
    confidence: float    # [0, 1]
    stability: float     # [0, 1] — inverse variance of recent supports

    # ── lifecycle ───────────────────────────────────────────────────
    status: BeliefStatus
    created_at: datetime
    updated_at: datetime

    # ── evidence ledger (the audit chain) ───────────────────────────
    # Every belief references the evidence that supports or counters it.
    # The ledger is append-only — supersession recorded in metadata,
    # but ids stay in place for full auditability.
    supporting_memory_ids: list[str] = field(default_factory=list)
    counterevidence_ids: list[str] = field(default_factory=list)

    # ── context lane (Block 6) ──────────────────────────────────────
    # None = global belief. Non-None = scoped belief in that lane.
    # Global and scoped beliefs coexist per guardrail B — neither
    # automatically rewrites the other.
    context_key: str | None = None

    # ── extension ───────────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── invariants ──────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if not self.belief_id:
            raise ValueError("belief_id is required")
        if not self.user_id:
            raise ValueError("user_id is required")
        if self.dimension not in DIMENSION_SPECS:
            raise ValueError(
                f"unknown dimension {self.dimension!r}. "
                f"Valid: {sorted(DIMENSION_SPECS)}"
            )
        if self.status not in VALID_BELIEF_STATUSES:
            raise ValueError(
                f"invalid status {self.status!r}. "
                f"Valid: {sorted(VALID_BELIEF_STATUSES)}"
            )
        spec = DIMENSION_SPECS[self.dimension]
        if not (spec.value_min <= self.value <= spec.value_max):
            raise ValueError(
                f"value {self.value} out of range for dimension "
                f"{self.dimension!r} [{spec.value_min}, {spec.value_max}]"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if not (0.0 <= self.stability <= 1.0):
            raise ValueError(f"stability must be in [0, 1], got {self.stability}")

    # ── derived properties ──────────────────────────────────────────

    @property
    def spec(self) -> DimensionSpec:
        return DIMENSION_SPECS[self.dimension]

    @property
    def support_count(self) -> int:
        return len(self.supporting_memory_ids)

    @property
    def contradiction_count(self) -> int:
        return len(self.counterevidence_ids)

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_stale(self) -> bool:
        return self.status == "stale"

    @property
    def is_invalidated(self) -> bool:
        return self.status == "invalidated"

    @property
    def is_global(self) -> bool:
        """True iff this is a global (context_key is None) belief."""
        return self.context_key is None

    @property
    def is_scoped(self) -> bool:
        """True iff this is a scoped (context_key is not None) belief."""
        return self.context_key is not None
