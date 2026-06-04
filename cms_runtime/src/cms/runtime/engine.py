"""
CMS Engine — top-level orchestrator.

Wires the L1 ObservationService → L2 EpisodeService → L3 EvidenceService
chain. This is the public entry point for runtime users.

Per the ADR:
  - The engine returns a structured turn_result, but does NOT format
    that result for any specific consumer (no LLM prompt, no agent
    routing schema, no UI rendering).
  - Evidence is filed on every turn (observation-level) and on episode
    closure (episode-level). No downstream consumption yet.

Block 3 wiring
--------------
On every turn:
  1. Observation ingested and persisted
  2. Evidence filed from the observation (5 rules)
  3. L2 update — optionally closes an episode
  4. If episode closed: evidence filed from the episode (3 rules)
  5. TurnResult returned with all new evidence ids

Future layers (Block 5 beliefs) will subscribe to evidence creation
here. The engine's public contract does not change.

Backward compatibility
----------------------
The evidence service is optional in the constructor. If not provided,
the engine falls back to Block 2 behavior (no evidence filing). This
keeps older tests and the minimal L1+L2 example in the README working
without forcing the evidence dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cms.l1.observation import L1Observation
from cms.l1.service import ObservationService
from cms.l2.episode import L2Episode
from cms.l2.service import EpisodeService
from cms.l3.belief_service import BeliefService
from cms.l3.service import EvidenceService


@dataclass(slots=True)
class TurnResult:
    """Result of processing one turn through the engine.

    Consumer-neutral: contains the canonical observation, optionally
    a closed episode (if this turn triggered a closure), the current
    open episode size for diagnostics, and the new evidence ids
    produced by this turn.

    Consumers may project this into their own representations
    (LLM context, dashboard payload, etc.) but the engine itself
    does not pick a representation.
    """

    observation: L1Observation
    closed_episode: L2Episode | None
    open_episode_size: int

    # Block 3: evidence ids produced by this turn.
    # Empty list if no evidence service is wired (pre-Block 3 configurations).
    new_evidence_ids: list[str] = field(default_factory=list)

    # Block 5: belief ids that were created or updated this turn.
    # Empty list if no belief service is wired.
    updated_belief_ids: list[str] = field(default_factory=list)


class CMSEngine:
    """Top-level runtime orchestrator.

    Usage:

        engine = CMSEngine(
            observation_service, episode_service,
            evidence_service, belief_service,
        )
        result = engine.process_turn("alice", "sess_1", "t0", "Hello.")

        if result.closed_episode is not None:
            ...
        if result.new_evidence_ids:
            ...
        if result.updated_belief_ids:
            ...
    """

    def __init__(
        self,
        observation_service: ObservationService,
        episode_service: EpisodeService,
        evidence_service: EvidenceService | None = None,
        belief_service: BeliefService | None = None,
    ):
        """
        Parameters
        ----------
        observation_service
            Required. Handles text → observation ingestion.
        episode_service
            Required. Handles episode boundary detection and persistence.
        evidence_service
            Optional. If provided, evidence is filed from every
            observation and every closed episode.
        belief_service
            Optional. If provided, beliefs are updated from new evidence
            on every turn. Requires evidence_service to be useful (no
            evidence → no belief updates).
        """
        self._obs_service = observation_service
        self._ep_service = episode_service
        self._ev_service = evidence_service
        self._belief_service = belief_service

    def process_turn(
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
        context_key: str | None = None,
    ) -> TurnResult:
        """Process one turn end-to-end.

        Steps:
          1. Ingest the utterance → L1Observation (persisted)
          2. File evidence from observation (if evidence service wired)
          3. Update the L2 episode tracker → optional L2Episode
          4. File evidence from closed episode (if any, and if wired)
          5. Update beliefs from new evidence (if belief service wired)
          6. Return the canonical TurnResult

        Pass turn_index when you need durable temporal phase across
        process restarts or multiple workers — see ObservationService.

        Block 6: pass `context_key` to scope this turn's evidence and
        beliefs into a named lane (e.g. "research", "ops"). Default
        None means global. The same context_key passed every turn of a
        session keeps that session in one lane; mid-session shifts are
        supported by changing the value across turns. Per guardrail B,
        scoped beliefs and global beliefs coexist with no implicit
        reconciliation.
        """
        obs = self._obs_service.ingest(
            user_id=user_id,
            session_id=session_id,
            turn_id=turn_id,
            text=text,
            language=language,
            tags=tags,
            entities=entities,
            metadata=metadata,
            turn_index=turn_index,
        )

        new_evidence_ids: list[str] = []
        new_evidence_records = []

        # Observation-level evidence
        if self._ev_service is not None:
            new_records = self._ev_service.file_from_observation(
                obs, context_key=context_key,
            )
            new_evidence_records.extend(new_records)
            new_evidence_ids.extend(r.memory_id for r in new_records)

        # L2 update
        closed = self._ep_service.update(obs)

        # Episode-level evidence
        if closed is not None and self._ev_service is not None:
            new_records = self._ev_service.file_from_episode(
                closed, context_key=context_key,
            )
            new_evidence_records.extend(new_records)
            new_evidence_ids.extend(r.memory_id for r in new_records)

        # Belief updates from new evidence (Block 5+6)
        updated_belief_ids: list[str] = []
        if self._belief_service is not None and new_evidence_records:
            updated = self._belief_service.process_new_evidence(new_evidence_records)
            updated_belief_ids = [b.belief_id for b in updated]

        return TurnResult(
            observation=obs,
            closed_episode=closed,
            open_episode_size=self._ep_service.open_size(user_id, session_id),
            new_evidence_ids=new_evidence_ids,
            updated_belief_ids=updated_belief_ids,
        )

    def end_session(
        self,
        user_id: str,
        session_id: str,
        *,
        context_key: str | None = None,
    ) -> L2Episode | None:
        """Force-close any open episode for a session.

        If an evidence service is wired, evidence is filed from the
        flushed episode just like on natural closure. If a belief
        service is also wired, those new evidence records feed belief
        updates as well.

        Pass `context_key` to scope flush-time evidence into a named
        lane. Defaults to None (global).
        """
        closed = self._ep_service.flush(user_id, session_id)
        if closed is not None and self._ev_service is not None:
            new_records = self._ev_service.file_from_episode(
                closed, context_key=context_key,
            )
            if self._belief_service is not None and new_records:
                self._belief_service.process_new_evidence(new_records)
        return closed
