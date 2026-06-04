"""
Integration test: full L1 → L2 → L3A → L3B pipeline via CMSEngine.

Proves:
  - turns produce evidence which produces beliefs
  - belief state surfaces in RuntimeStateView
  - StateAssembler reads beliefs without mutating them (guardrail B)
  - belief lifecycle: tentative → active → contradiction → invalidated
  - cross-session belief persistence
"""

from datetime import timedelta
from itertools import count

import pytest

from cms.l1.adapter import LegacyExtractorAdapter
from cms.l1.service import ObservationService
from cms.l2.policies import WindowedClosurePolicy
from cms.l2.service import EpisodeService
from cms.l3.belief_policy import BeliefThresholds
from cms.l3.belief_service import BeliefService
from cms.l3.service import EvidenceService
from cms.runtime.assembler import StateAssembler
from cms.runtime.engine import CMSEngine
from cms.runtime.retrieval import RetrievalService
from cms.storage.belief_store import BeliefStore
from cms.storage.episode_store import EpisodeStore
from cms.storage.evidence_store import EvidenceStore
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


class TriggeringExtractor:
    """Extractor with directed prefixes for predictable rule firing."""
    def extract_sentence_features(self, sentence: str) -> dict[str, float]:
        features = {
            "semantic_density": 0.5, "pragmatic_load": 0.3,
            "epistemic_certainty": 0.5, "temporal_orientation": 0.5,
            "topic_concreteness": 0.5, "intent_direction": 0.5,
        }
        if sentence.startswith("CERT:"):
            features["epistemic_certainty"] = 0.95
        elif sentence.startswith("HEDGE:"):
            features["epistemic_certainty"] = 0.1
        elif sentence.startswith("OTHER:"):
            features["intent_direction"] = 0.95
        elif sentence.startswith("SELF:"):
            features["intent_direction"] = 0.1
        elif sentence.startswith("PRAG:"):
            features["semantic_density"] = 0.2
            features["pragmatic_load"] = 0.9
        return features


@pytest.fixture
def stack():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    obs_store = ObservationStore(backend)
    ep_store = EpisodeStore(backend)
    ev_store = EvidenceStore(backend)
    b_store = BeliefStore(backend)

    adapter = LegacyExtractorAdapter(TriggeringExtractor())
    obs_counter = count(0)
    ep_counter = count(0)
    ev_counter = count(0)
    b_counter = count(0)

    obs_service = ObservationService(
        adapter=adapter, store=obs_store,
        id_factory=lambda: f"obs_{next(obs_counter):03d}",
    )
    ep_service = EpisodeService(
        store=ep_store,
        policy=WindowedClosurePolicy(max_size=100),  # don't auto-close
        id_factory=lambda: f"ep_{next(ep_counter):03d}",
    )
    ev_service = EvidenceService(
        store=ev_store,
        id_factory=lambda: f"mem_{next(ev_counter):04d}",
    )
    # Lower the activation threshold for clarity in tests
    thresholds = BeliefThresholds(
        active_min_support=3,
        active_min_confidence=0.5,
    )
    belief_service = BeliefService(
        belief_store=b_store, evidence_store=ev_store,
        thresholds=thresholds,
        id_factory=lambda: f"b_{next(b_counter):04d}",
    )
    engine = CMSEngine(obs_service, ep_service, ev_service, belief_service)

    retrieval = RetrievalService(obs_store, ep_store, ev_store)
    assembler = StateAssembler(retrieval, belief_store=b_store)

    yield engine, assembler, b_store, ev_store
    backend.close()


# ── belief creation through the engine ───────────────────────────────


class TestBeliefCreation:
    def test_certainty_turn_creates_tentative_epistemic_belief(self, stack):
        engine, _, b_store, _ = stack
        result = engine.process_turn("alice", "s1", "t0", "CERT: definite.")

        assert len(result.updated_belief_ids) == 1
        belief = b_store.get(result.updated_belief_ids[0])
        assert belief.dimension == "epistemic_style"
        assert belief.status == "tentative"

    def test_three_certainty_turns_promote_to_active(self, stack):
        engine, _, b_store, _ = stack
        for i in range(3):
            engine.process_turn("alice", "s1", f"t{i}", "CERT: yes.")

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief is not None
        assert belief.status == "active"

    def test_cross_dimension_independence(self, stack):
        engine, _, b_store, _ = stack
        engine.process_turn("alice", "s1", "t0", "CERT: definite.")
        engine.process_turn("alice", "s1", "t1", "OTHER: help me?")
        engine.process_turn("alice", "s1", "t2", "PRAG: please?")

        beliefs = b_store.list_for_user("alice")
        dims = {b.dimension for b in beliefs}
        assert dims == {"epistemic_style", "social_orientation", "pragmatic_style"}


# ── contradiction through the engine ─────────────────────────────────


class TestContradictionThroughEngine:
    def test_hedging_after_certainty_recorded_as_contradiction(self, stack):
        engine, _, b_store, _ = stack
        for i in range(3):
            engine.process_turn("alice", "s1", f"t{i}", "CERT: yes.")
        engine.process_turn("alice", "s1", "t_hedge", "HEDGE: maybe?")

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.contradiction_count == 1

    def test_overwhelming_contradictions_invalidate_belief(self, stack):
        engine, _, b_store, _ = stack
        engine.process_turn("alice", "s1", "t0", "CERT: yes.")
        for i in range(3):
            engine.process_turn("alice", "s1", f"t{i+1}", "HEDGE: maybe?")

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.status == "invalidated"


# ── state assembly with beliefs ──────────────────────────────────────


class TestStateAssemblyWithBeliefs:
    def test_active_beliefs_surface_in_view(self, stack):
        engine, assembler, _, _ = stack
        for i in range(3):
            engine.process_turn("alice", "s1", f"t{i}", "CERT: yes.")

        view = assembler.build("alice", "s1")
        assert len(view.active_beliefs) == 1
        assert view.active_beliefs[0].dimension == "epistemic_style"
        assert view.freshness_flags["has_active_beliefs"] is True

    def test_tentative_beliefs_surface_separately(self, stack):
        engine, assembler, _, _ = stack
        # One support — tentative
        engine.process_turn("alice", "s1", "t0", "CERT: yes.")

        view = assembler.build("alice", "s1")
        assert len(view.active_beliefs) == 0
        assert len(view.tentative_beliefs) == 1

    def test_invalidated_beliefs_excluded_from_view(self, stack):
        engine, assembler, _, _ = stack
        engine.process_turn("alice", "s1", "t0", "CERT: yes.")
        for i in range(3):
            engine.process_turn("alice", "s1", f"t{i+1}", "HEDGE: maybe?")

        view = assembler.build("alice", "s1")
        # No active or tentative — invalidated belief excluded
        assert len(view.active_beliefs) == 0
        assert len(view.tentative_beliefs) == 0
        # But counted
        assert view.counts["invalidated_beliefs"] == 1

    def test_state_view_uses_real_persisted_belief_ids(self, stack):
        engine, assembler, b_store, _ = stack
        for i in range(3):
            engine.process_turn("alice", "s1", f"t{i}", "CERT: yes.")

        view = assembler.build("alice", "s1")
        for belief in view.active_beliefs:
            assert b_store.get(belief.belief_id) is not None


# ── guardrail B: assembler is read-only ──────────────────────────────


class TestAssemblerReadOnly:
    def test_assembly_does_not_modify_beliefs(self, stack):
        engine, assembler, b_store, _ = stack
        for i in range(3):
            engine.process_turn("alice", "s1", f"t{i}", "CERT: yes.")

        belief_before = b_store.get_for_user_dimension("alice", "epistemic_style")
        # Snapshot the belief state
        before_snapshot = {
            "value": belief_before.value,
            "confidence": belief_before.confidence,
            "stability": belief_before.stability,
            "status": belief_before.status,
            "support_count": belief_before.support_count,
            "updated_at": belief_before.updated_at,
        }

        # Build state multiple times — must not mutate belief
        for _ in range(5):
            assembler.build("alice", "s1")

        belief_after = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief_after.value == before_snapshot["value"]
        assert belief_after.confidence == before_snapshot["confidence"]
        assert belief_after.stability == before_snapshot["stability"]
        assert belief_after.status == before_snapshot["status"]
        assert belief_after.support_count == before_snapshot["support_count"]
        assert belief_after.updated_at == before_snapshot["updated_at"]


# ── cross-session belief persistence ─────────────────────────────────


class TestCrossSessionPersistence:
    def test_beliefs_persist_across_sessions(self, stack):
        engine, assembler, b_store, _ = stack
        for i in range(3):
            engine.process_turn("alice", "s1", f"t{i}", "CERT: yes.")

        # End first session, start a new one
        engine.end_session("alice", "s1")
        engine.process_turn("alice", "s2", "t0", "CERT: more.")

        # Beliefs from s1 still exist; new evidence from s2 added to ledger
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.support_count >= 4


# ── back-compat: engine without belief service ───────────────────────


class TestBackCompat:
    def test_engine_without_belief_service_works(self):
        backend = SQLiteBackend(":memory:")
        backend.bootstrap_schema(FULL_SCHEMA_DDL)
        adapter = LegacyExtractorAdapter(TriggeringExtractor())
        obs_service = ObservationService(
            adapter=adapter, store=ObservationStore(backend),
        )
        ep_service = EpisodeService(
            store=EpisodeStore(backend),
            policy=WindowedClosurePolicy(max_size=100),
        )
        ev_service = EvidenceService(store=EvidenceStore(backend))
        # No belief_service argument
        engine = CMSEngine(obs_service, ep_service, ev_service)

        result = engine.process_turn("alice", "s1", "t0", "CERT: yes.")
        # Belief ids list defaults to empty
        assert result.updated_belief_ids == []
        # But evidence is still filed
        assert len(result.new_evidence_ids) >= 1
        backend.close()
