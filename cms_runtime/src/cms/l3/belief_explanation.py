"""
Belief explanations — Block 6.

Per the locked contract:
  - structured payload, never LLM-generated prose
  - on demand via belief_service.explain(belief_id)
  - top supporting/counterevidence ranked deterministically, capped at 5
  - NOT carried on RuntimeStateView (consumers fetch on demand)

The dataclass is a passive structure — explanations are computed
fresh by BeliefService.explain() each call. There's no caching here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class BeliefExplanation:
    """Structured, deterministic, non-prose explanation of a belief's state.

    Top supporting and counterevidence ids are ranked by:
        1. support_score DESC
        2. created_at DESC
        3. memory_id DESC (deterministic tie-break)
    and capped at top_n (default 5).
    """

    belief_id: str
    user_id: str
    dimension: str
    context_key: str | None
    status: str
    value: float
    confidence: float
    stability: float

    support_count: int                # count of active (non-superseded) supports
    contradiction_count: int          # count of active (non-superseded) contras
    superseded_count: int             # count of superseded records in the ledger

    latest_support_at: datetime | None
    latest_contradiction_at: datetime | None

    top_supporting_memory_ids: list[str] = field(default_factory=list)
    top_counterevidence_ids: list[str] = field(default_factory=list)
