"""
Observation service — entry point for ingesting utterances into the runtime.

Responsibility (single):
  Convert (user_id, session_id, turn_id, text) → persisted L1Observation.

Out of scope for this slice:
  - episode boundary detection
  - evidence filing
  - belief updates
  - retrieval
  - context assembly

These are explicitly deferred per the ADR sequencing decision.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from cms.l1.adapter import LegacyExtractorAdapter
from cms.l1.observation import L1Observation

if TYPE_CHECKING:
    from cms.storage.observation_store import ObservationStore


class ObservationService:
    """Ingest one utterance, produce + persist one L1Observation."""

    def __init__(
        self,
        adapter: LegacyExtractorAdapter,
        store: "ObservationStore",
        *,
        clock: callable = None,
        id_factory: callable = None,
    ):
        """
        Parameters
        ----------
        adapter
            Feature extractor adapter (wraps research extractor).
        store
            Persistence backend implementing ObservationStore.
        clock
            Callable returning a timezone-aware datetime. Injectable
            for tests. Defaults to datetime.now(timezone.utc).
        id_factory
            Callable returning a unique obs_id string. Injectable for
            tests. Defaults to uuid4 hex.
        """
        self._adapter = adapter
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        # Per-session turn counter — used for temporal_phase computation
        # when no explicit turn index is supplied by the caller.
        self._turn_counters: dict[str, int] = {}

    def ingest(
        self,
        user_id: str,
        session_id: str,
        turn_id: str,
        text: str,
        *,
        language: str | None = None,
        tags: list[str] | None = None,
        entities: list[str] | None = None,
        metadata: dict | None = None,
        turn_index: int | None = None,
    ) -> L1Observation:
        """Process one utterance into a persisted L1Observation.

        Parameters
        ----------
        user_id, session_id, turn_id
            Routing identity for the utterance.
        text
            Raw utterance text.
        language
            ISO 639-1 code if known (e.g. 'en', 'fr'). Optional.
        tags, entities
            Free strings — no taxonomy enforcement at this layer.
        metadata
            Free-form extension dict.
        turn_index
            Optional explicit position of this turn within the session.
            When provided, this is used to compute temporal_phase. When
            absent, an in-memory per-session counter is used as a fallback.

            Callers that need durable phase semantics across process
            restarts or across multiple workers SHOULD supply turn_index
            explicitly (e.g., from an external session log).

        Returns
        -------
        The persisted L1Observation.
        """
        # Resolve turn_index: explicit value wins, fallback to internal counter
        session_key = f"{user_id}::{session_id}"
        if turn_index is None:
            turn_index = self._turn_counters.get(session_key, 0)
            self._turn_counters[session_key] = turn_index + 1
        else:
            # Update the counter so subsequent fallback turns continue from here
            self._turn_counters[session_key] = max(
                self._turn_counters.get(session_key, 0), turn_index + 1
            )

        # Extract features and CMS coords via adapter
        cms_real, cms_imag, features = self._adapter.encode(text)
        temporal_phase = self._adapter.compute_temporal_phase(turn_index)

        # Compute simple quality signals — kept minimal for the slice
        quality = {
            "text_length": float(len(text)),
            "feature_coverage": float(
                sum(1 for v in features.values() if v != 0.0) / max(len(features), 1)
            ),
        }

        obs = L1Observation(
            obs_id=self._id_factory(),
            user_id=user_id,
            session_id=session_id,
            turn_id=turn_id,
            created_at=self._clock(),
            raw_text=text,
            language=language,
            cms_real=cms_real,
            cms_imag=cms_imag,
            temporal_phase=temporal_phase,
            features=features,
            tags=list(tags or []),
            entities=list(entities or []),
            quality=quality,
            metadata=dict(metadata or {}),
        )

        self._store.save(obs)
        return obs
