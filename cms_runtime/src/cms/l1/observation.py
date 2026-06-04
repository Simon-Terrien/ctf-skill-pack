"""
L1 Observation — canonical evidence unit at the utterance level.

An L1Observation is the immutable record of one utterance's interpretation
at the moment it was processed. It captures:

  - identity     : obs_id, user_id, session_id, turn_id, created_at
  - source       : raw_text, language
  - cms encoding : real and imaginary components of the complex meaning
                   space embedding, plus temporal phase
  - features     : interpretable linguistic feature dict
  - context      : tags, entities (free strings — no fixed taxonomy yet,
                   per ADR scope decision)
  - quality      : per-feature confidence/coverage signals
  - metadata     : free-form extension point

Design notes
------------
Per the ADR:
  - No fixed semantic taxonomy is hard-coded at this layer.
  - Tags and entities are free strings to allow patterns to emerge.
  - Storage is delegated to ObservationStore (see cms.storage).
  - This dataclass is consumer-neutral: it does not assume LLM context,
    agent routing, or any specific downstream use.

The CMS encoding is stored as two parallel float lists rather than
Python complex objects to keep serialization trivial across SQLite,
JSON, and any future transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class L1Observation:
    """Immutable record of one utterance's interpretation."""

    # ── identity ────────────────────────────────────────────────────
    obs_id: str
    user_id: str
    session_id: str
    turn_id: str
    created_at: datetime

    # ── source ──────────────────────────────────────────────────────
    raw_text: str
    language: str | None

    # ── CMS encoding ────────────────────────────────────────────────
    # cms_real[i] + 1j * cms_imag[i] for each CMS dimension.
    # Length must match across the two lists.
    cms_real: list[float]
    cms_imag: list[float]
    temporal_phase: float

    # ── feature dict ────────────────────────────────────────────────
    # Linguistic features extracted from raw_text (semantic_density,
    # pragmatic_load, epistemic_certainty, etc.). Free key/value to
    # avoid premature schema lock-in.
    features: dict[str, float] = field(default_factory=dict)

    # ── context ─────────────────────────────────────────────────────
    # Free strings per ADR — no fixed taxonomy at this layer.
    tags: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)

    # ── quality signals ─────────────────────────────────────────────
    # Per-feature or per-extraction quality scores
    # (e.g. {"text_length": 142.0, "feature_coverage": 0.83}).
    quality: dict[str, float] = field(default_factory=dict)

    # ── extension point ─────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── invariants ──────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if len(self.cms_real) != len(self.cms_imag):
            raise ValueError(
                f"CMS dimension mismatch: "
                f"len(cms_real)={len(self.cms_real)}, "
                f"len(cms_imag)={len(self.cms_imag)}"
            )
        if not self.obs_id:
            raise ValueError("obs_id is required")
        if not self.user_id:
            raise ValueError("user_id is required")
        if not self.session_id:
            raise ValueError("session_id is required")

    # ── convenience accessors ───────────────────────────────────────

    @property
    def cms_dim(self) -> int:
        """Number of CMS dimensions."""
        return len(self.cms_real)

    def to_complex(self) -> list[complex]:
        """Return the CMS encoding as a list of complex numbers."""
        return [complex(r, i) for r, i in zip(self.cms_real, self.cms_imag)]
