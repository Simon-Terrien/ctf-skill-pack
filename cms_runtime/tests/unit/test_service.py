"""Unit tests for ObservationService ingestion logic."""

from datetime import datetime, timezone
from itertools import count

import pytest

from cms.l1.adapter import LegacyExtractorAdapter
from cms.l1.service import ObservationService
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import OBSERVATIONS_DDL
from cms.storage.sqlite import SQLiteBackend


class StubExtractor:
    """Returns the same canned features for every call."""

    def __init__(self, features: dict[str, float] | None = None):
        self.features = features or {
            "semantic_density": 0.5,
            "pragmatic_load": 0.3,
            "epistemic_certainty": 0.7,
            "temporal_orientation": 0.4,
            "topic_concreteness": 0.6,
            "intent_direction": 0.5,
        }

    def extract_sentence_features(self, sentence: str) -> dict[str, float]:
        return dict(self.features)


@pytest.fixture
def store():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(OBSERVATIONS_DDL)
    yield ObservationStore(backend)
    backend.close()


@pytest.fixture
def fixed_clock():
    """Returns a constant datetime for deterministic testing."""
    return lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def deterministic_ids():
    """Returns sequential ids: obs_0, obs_1, ..."""
    counter = count(0)
    return lambda: f"obs_{next(counter)}"


@pytest.fixture
def service(store, fixed_clock, deterministic_ids):
    adapter = LegacyExtractorAdapter(StubExtractor())
    return ObservationService(
        adapter=adapter, store=store,
        clock=fixed_clock, id_factory=deterministic_ids,
    )


class TestBasicIngestion:
    def test_ingest_returns_observation(self, service):
        obs = service.ingest("alice", "s1", "t0", "Hello.")
        assert obs.user_id == "alice"
        assert obs.session_id == "s1"
        assert obs.turn_id == "t0"
        assert obs.raw_text == "Hello."

    def test_ingest_persists_to_store(self, service, store):
        obs = service.ingest("alice", "s1", "t0", "Hello.")
        retrieved = store.get(obs.obs_id)
        assert retrieved is not None
        assert retrieved.raw_text == "Hello."

    def test_uses_injected_clock(self, service, fixed_clock):
        obs = service.ingest("alice", "s1", "t0", "Hello.")
        assert obs.created_at == fixed_clock()

    def test_uses_injected_id_factory(self, service):
        obs1 = service.ingest("alice", "s1", "t0", "First.")
        obs2 = service.ingest("alice", "s1", "t1", "Second.")
        assert obs1.obs_id == "obs_0"
        assert obs2.obs_id == "obs_1"


class TestCMSEncoding:
    def test_cms_coords_match_extractor_output(self, service):
        obs = service.ingest("alice", "s1", "t0", "text")
        # Stub returns sem=0.5, prag=0.3, cert=0.7, temp=0.4, conc=0.6, intent=0.5
        assert obs.cms_real == [0.5, 0.7, 0.6]
        assert obs.cms_imag == [0.3, 0.4, 0.5]

    def test_cms_dimensions_consistent(self, service):
        obs = service.ingest("alice", "s1", "t0", "text")
        assert obs.cms_dim == 3
        assert len(obs.cms_real) == len(obs.cms_imag)


class TestTemporalPhaseCounter:
    def test_phase_advances_within_session(self, service):
        obs0 = service.ingest("alice", "s1", "t0", "a")
        obs1 = service.ingest("alice", "s1", "t1", "b")
        obs2 = service.ingest("alice", "s1", "t2", "c")
        assert obs0.temporal_phase < obs1.temporal_phase < obs2.temporal_phase

    def test_phase_isolated_per_session(self, service):
        # Two observations in s1, one in s2
        service.ingest("alice", "s1", "t0", "a")
        obs_s1 = service.ingest("alice", "s1", "t1", "b")
        obs_s2 = service.ingest("alice", "s2", "t0", "c")

        # First in s2 should have phase 0 (new session counter)
        assert obs_s2.temporal_phase == 0.0
        assert obs_s1.temporal_phase > 0.0

    def test_phase_isolated_per_user(self, service):
        service.ingest("alice", "s1", "t0", "a")
        obs_alice = service.ingest("alice", "s1", "t1", "b")
        obs_bob = service.ingest("bob", "s1", "t0", "c")

        # Bob's first observation should start at phase 0
        assert obs_bob.temporal_phase == 0.0
        assert obs_alice.temporal_phase > 0.0


class TestExplicitTurnIndex:
    """Callers can supply turn_index for durable phase semantics."""

    def test_explicit_turn_index_used_when_provided(self, service):
        # turn_index=10 should produce the same phase as the 11th implicit turn
        obs_explicit = service.ingest("alice", "s1", "t10", "x", turn_index=10)
        # Reset state for fair comparison
        service.reset_counter_for_test = None  # marker; not used

        backend2 = SQLiteBackend(":memory:")
        backend2.bootstrap_schema(OBSERVATIONS_DDL)
        store2 = ObservationStore(backend2)
        adapter = LegacyExtractorAdapter(StubExtractor())
        service2 = ObservationService(adapter=adapter, store=store2)
        # Burn through 10 implicit turns to reach turn_index=10
        for i in range(10):
            service2.ingest("alice", "s1", f"t{i}", "x")
        obs_implicit = service2.ingest("alice", "s1", "t10", "x")
        backend2.close()

        assert obs_explicit.temporal_phase == obs_implicit.temporal_phase

    def test_explicit_index_advances_internal_counter(self, service):
        """After an explicit turn_index, subsequent fallback turns continue from there."""
        service.ingest("alice", "s1", "t5", "explicit", turn_index=5)
        obs_next = service.ingest("alice", "s1", "t6", "implicit")
        # The next implicit turn should be at index 6, not 1
        # We verify via the phase value relative to turn 0
        from cms.l1.adapter import LegacyExtractorAdapter
        expected_phase = LegacyExtractorAdapter.compute_temporal_phase(6)
        assert obs_next.temporal_phase == expected_phase

    def test_explicit_index_does_not_regress_counter(self, service):
        """Supplying a smaller turn_index than current must not rewind the counter."""
        # Implicit: counter advances to 3
        for i in range(3):
            service.ingest("alice", "s1", f"t{i}", "x")
        # Explicit small turn_index
        service.ingest("alice", "s1", "t0_replay", "replay", turn_index=0)
        # Next implicit should still continue from at least 3
        obs = service.ingest("alice", "s1", "t_next", "next")
        from cms.l1.adapter import LegacyExtractorAdapter
        expected = LegacyExtractorAdapter.compute_temporal_phase(3)
        assert obs.temporal_phase == expected

    def test_durability_scenario_replay_yields_same_phase(self):
        """Process restart simulation: same turn_index → same phase."""
        from cms.l1.adapter import LegacyExtractorAdapter
        # First "process"
        backend_a = SQLiteBackend(":memory:")
        backend_a.bootstrap_schema(OBSERVATIONS_DDL)
        store_a = ObservationStore(backend_a)
        adapter_a = LegacyExtractorAdapter(StubExtractor())
        service_a = ObservationService(adapter=adapter_a, store=store_a)
        obs_first = service_a.ingest("alice", "s1", "turn_42", "text", turn_index=42)
        backend_a.close()

        # "Process restart" — fresh service, no in-memory state
        backend_b = SQLiteBackend(":memory:")
        backend_b.bootstrap_schema(OBSERVATIONS_DDL)
        store_b = ObservationStore(backend_b)
        adapter_b = LegacyExtractorAdapter(StubExtractor())
        service_b = ObservationService(adapter=adapter_b, store=store_b)
        obs_replayed = service_b.ingest("alice", "s1", "turn_42", "text", turn_index=42)
        backend_b.close()

        # Phase is durable across "restart" because turn_index is explicit
        assert obs_first.temporal_phase == obs_replayed.temporal_phase


class TestQualitySignals:
    def test_text_length_recorded(self, service):
        obs = service.ingest("alice", "s1", "t0", "Hello world.")
        assert obs.quality["text_length"] == 12.0

    def test_feature_coverage_recorded(self, service):
        obs = service.ingest("alice", "s1", "t0", "text")
        # All 6 stub features are non-zero → coverage = 1.0
        assert obs.quality["feature_coverage"] == 1.0

    def test_feature_coverage_partial(self):
        # Only 2 of 6 features non-zero
        partial = StubExtractor({
            "semantic_density": 0.5,
            "pragmatic_load": 0.0,
            "epistemic_certainty": 0.0,
            "temporal_orientation": 0.0,
            "topic_concreteness": 0.0,
            "intent_direction": 0.3,
        })
        backend = SQLiteBackend(":memory:")
        backend.bootstrap_schema(OBSERVATIONS_DDL)
        store = ObservationStore(backend)
        adapter = LegacyExtractorAdapter(partial)
        service = ObservationService(adapter, store)

        obs = service.ingest("alice", "s1", "t0", "text")
        assert obs.quality["feature_coverage"] == pytest.approx(2 / 6)
        backend.close()


class TestPassthroughFields:
    def test_tags_passed_through(self, service):
        obs = service.ingest("alice", "s1", "t0", "text", tags=["important", "review"])
        assert obs.tags == ["important", "review"]

    def test_entities_passed_through(self, service):
        obs = service.ingest("alice", "s1", "t0", "text", entities=["server", "API"])
        assert obs.entities == ["server", "API"]

    def test_language_passed_through(self, service):
        obs = service.ingest("alice", "s1", "t0", "Bonjour", language="fr")
        assert obs.language == "fr"

    def test_metadata_passed_through(self, service):
        meta = {"source": "slack", "channel": "engineering"}
        obs = service.ingest("alice", "s1", "t0", "text", metadata=meta)
        assert obs.metadata == meta

    def test_defaults_when_optional_omitted(self, service):
        obs = service.ingest("alice", "s1", "t0", "text")
        assert obs.tags == []
        assert obs.entities == []
        assert obs.language is None
        assert obs.metadata == {}
