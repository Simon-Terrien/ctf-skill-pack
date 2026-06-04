"""
Migration adapter — convert legacy CMSPoint records to L1Observation.

Per the ADR, the research line stays operational. This module exists
so that:

  1. Existing research workflows that produced CMSPoint objects can
     be reprocessed through the runtime to backfill observation history.

  2. Equivalence between the two paths can be tested explicitly.

Important
---------
This adapter only handles the *encoding* equivalence (CMS coordinates).
It does NOT carry over derived dynamics, signatures, or personality
traits — those belong to the research line and to later runtime layers
(episodes, evidence, beliefs).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from cms.l1.observation import L1Observation


class CMSPointLike(Protocol):
    """Structural protocol matching the research-line CMSPoint dataclass."""

    z1: complex
    z2: complex
    z3: complex
    t: float


def cms_point_to_observation(
    point: CMSPointLike,
    *,
    user_id: str,
    session_id: str,
    turn_id: str | None = None,
    raw_text: str = "",
    created_at: datetime | None = None,
    obs_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> L1Observation:
    """Convert a research-line CMSPoint into a runtime L1Observation.

    Parameters
    ----------
    point
        A CMSPoint-like object with z1, z2, z3 (complex) and t (float).
    user_id, session_id
        Routing identity. Required.
    turn_id
        If not given, derived from `point.t`.
    raw_text
        Original utterance if available. Empty string if not preserved
        in the source data.
    created_at
        Timezone-aware datetime. Defaults to now (UTC).
    obs_id
        Optional explicit id. Generated if absent.
    metadata
        Free-form passthrough. The migration source is recorded under
        the 'migration' key automatically.

    Notes
    -----
    Fields not derivable from CMSPoint (features dict, tags, entities,
    quality, language) are left empty. This is honest: we do not have
    that information in the legacy data. Consumers should not assume
    those fields are populated for migrated observations.
    """
    cms_real = [float(point.z1.real), float(point.z2.real), float(point.z3.real)]
    cms_imag = [float(point.z1.imag), float(point.z2.imag), float(point.z3.imag)]

    meta = dict(metadata or {})
    meta.setdefault("migration", {"source": "research_line.CMSPoint", "t": point.t})

    return L1Observation(
        obs_id=obs_id or uuid.uuid4().hex,
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id or f"t_{int(point.t)}",
        created_at=created_at or datetime.now(timezone.utc),
        raw_text=raw_text,
        language=None,
        cms_real=cms_real,
        cms_imag=cms_imag,
        temporal_phase=0.0,  # legacy data has no phase encoding
        features={},
        tags=[],
        entities=[],
        quality={"migrated": 1.0},
        metadata=meta,
    )
