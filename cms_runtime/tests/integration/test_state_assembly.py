"""
Integration test: full L1 → L2 → L3 → State assembly pipeline.

Proves that after running turns through the engine, the StateAssembler
sees the persisted state correctly and produces a coherent RuntimeStateView.

Key invariants validated:
  - state assembly after turn processing surfaces the just-filed evidence
  - state assembly is deterministic across repeated calls
  - state assembly is consumer-neutral
  - all referenced ids in the view exist in the underlying stores
"""

from itertools import count

import pytest

from cms.l1.adapter import LegacyExtractorAdapter
from cms.l1.service import ObservationService
from cms.l2.policies import WindowedClosurePolicy
from cms.l2.service import EpisodeService
from cms.l3.service import EvidenceService
from cms.runtime.assembler import StateAssembler
from cms.runtime.engine import CMSEngine
from cms.runtime.retrieval import RetrievalService
from cms.storage.episode_store import EpisodeStore
from cms.storage.evidence_store import EvidenceStore
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


class TriggeringExtractor:
    """Same as Block 3's integration extractor."""
    def extract_sentence_features(self, sentence: str) -> dict[str, float]:
        features = {
            "semantic_density": 0.5, "pragmatic_load": 0.3,
            "epistemic_certainty": 0.5, "temporal_orientation": 0.5,
            "topic_concreteness": 0.5, "intent_direction": 0.5,
        }
        if sentence.startswith("CERT:"):
            features["epistemic_certainty"] = 0.9
        elif sentence.startswith("HEDGE:"):
            features["epistemic_certainty"] = 0.2
        elif sentence.startswith("OTHER:"):
            features["intent_direction"] = 0.9
        elif sentence.startswith("PRAG:"):
            features["semantic_density"] = 0.2
            features["pragmatic_load"] = 0.8
        return features


@pytest.fixture
def stack():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    obs_store = ObservationStore(backend)
    ep_store = EpisodeStore(backend)
    ev_store = EvidenceStore(backend)

    adapter = LegacyExtractorAdapter(TriggeringExtractor())
    obs_counter = count(0)
    ep_counter = count(0)
    ev_counter = count(0)

    obs_service = ObservationService(
        adapter=adapter, store=obs_store,
        id_factory=lambda: f"obs_{next(obs_counter):03d}",
    )
    ep_service = EpisodeService(
        store=ep_store,
        policy=WindowedClosurePolicy(max_size=3),
        id_factory=lambda: f"ep_{next(ep_counter):03d}",
    )
    ev_service = EvidenceService(
        store=ev_store,
        id_factory=lambda: f"mem_{next(ev_counter):04d}",
    )
    engine = CMSEngine(obs_service, ep_service, ev_service)

    retrieval = RetrievalService(obs_store, ep_store, ev_store)
    assembler = StateAssembler(retrieval)

    yield engine, assembler, obs_store, ep_store, ev_store
    backend.close()


# ── post-turn state assembly ─────────────────────────────────────────


class TestStateAfterTurns:
    def test_assembly_after_single_turn_sees_observation(self, stack):
        engine, assembler, _, _, _ = stack
        result = engine.process_turn("alice", "s1", "t0", "CERT: yes.")

        view = assembler.build("alice", "s1")
        assert view.current_observation is not None
        assert view.current_observation.obs_id == result.observation.obs_id
        assert view.counts["recent_observations"] == 1

    def test_assembly_surfaces_just_filed_evidence(self, stack):
        engine, assembler, _, _, _ = stack
        result = engine.process_turn("alice", "s1", "t0", "CERT: certain.")

        view = assembler.build("alice", "s1")
        # Evidence ids reported by the engine should appear in the view
        for mem_id in result.new_evidence_ids:
            assert mem_id in view.retrieved_evidence_ids

    def test_assembly_after_multiple_turns_sees_episodes(self, stack):
        engine, assembler, _, _, _ = stack
        # 4 turns → window of 3 → at least one episode closes
        for i in range(4):
            engine.process_turn("alice", "s1", f"t{i}", f"CERT: turn {i}.")

        view = assembler.build("alice", "s1")
        assert view.counts["recent_episodes"] >= 1


class TestProvenanceChainAfterAssembly:
    def test_every_view_id_exists_in_store(self, stack):
        engine, assembler, obs_store, ep_store, ev_store = stack
        for i in range(6):
            prefix = ["CERT:", "HEDGE:", "OTHER:", "PRAG:", "CERT:", "HEDGE:"][i]
            engine.process_turn("alice", "s1", f"t{i}", f"{prefix} text.")

        view = assembler.build("alice", "s1")

        for obs_id in view.recent_observation_ids:
            assert obs_store.get(obs_id) is not None, f"missing obs {obs_id}"
        for ep_id in view.recent_episode_ids:
            assert ep_store.get(ep_id) is not None, f"missing ep {ep_id}"
        for mem_id in view.retrieved_evidence_ids:
            assert ev_store.get(mem_id) is not None, f"missing evidence {mem_id}"


class TestDeterminism:
    def test_repeated_assembly_identical(self, stack):
        engine, assembler, _, _, _ = stack
        for i in range(5):
            engine.process_turn("alice", "s1", f"t{i}", f"CERT: turn {i}.")

        view_a = assembler.build("alice", "s1")
        view_b = assembler.build("alice", "s1")

        assert view_a.recent_observation_ids == view_b.recent_observation_ids
        assert view_a.recent_episode_ids == view_b.recent_episode_ids
        assert view_a.retrieved_evidence_ids == view_b.retrieved_evidence_ids


class TestScopeFilteringAfterTurns:
    def test_scope_filter_reflects_turn_content(self, stack):
        engine, assembler, _, _, _ = stack
        engine.process_turn("alice", "s1", "t0", "CERT: definite.")
        engine.process_turn("alice", "s1", "t1", "PRAG: please?")

        epistemic_view = assembler.build("alice", "s1", scope="epistemic")
        pragmatic_view = assembler.build("alice", "s1", scope="pragmatic")

        # Each scope should yield only its own evidence
        for ev in epistemic_view.retrieved_evidence:
            assert ev.scope == "epistemic"
        for ev in pragmatic_view.retrieved_evidence:
            assert ev.scope == "pragmatic"


class TestConsumerNeutrality:
    def test_no_consumer_specific_fields_in_view(self, stack):
        engine, assembler, _, _, _ = stack
        engine.process_turn("alice", "s1", "t0", "CERT: yes.")
        view = assembler.build("alice", "s1")

        # Verify the view contains canonical fields, no consumer-specific ones.
        # Block 6 split: active/tentative beliefs are now exposed as
        # *_global / *_scoped pairs (active_beliefs and tentative_beliefs
        # remain as backward-compat properties returning the global lists).
        canonical_fields = {
            "user_id", "session_id", "current_observation",
            "recent_observations", "recent_episodes", "retrieved_evidence",
            "active_beliefs_global", "active_beliefs_scoped",
            "tentative_beliefs_global", "tentative_beliefs_scoped",
            "signals", "counts", "freshness_flags",
        }
        actual_fields = set(view.__slots__)
        assert actual_fields == canonical_fields
