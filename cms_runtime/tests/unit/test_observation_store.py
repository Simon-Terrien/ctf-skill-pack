"""Unit tests for ObservationStore CRUD operations against in-memory SQLite."""

from datetime import datetime, timezone

import pytest

from cms.l1.observation import L1Observation
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import OBSERVATIONS_DDL
from cms.storage.sqlite import SQLiteBackend


@pytest.fixture
def backend():
    """Fresh in-memory SQLite backend with bootstrapped schema."""
    be = SQLiteBackend(":memory:")
    be.bootstrap_schema(OBSERVATIONS_DDL)
    yield be
    be.close()


@pytest.fixture
def store(backend):
    return ObservationStore(backend)


def make_obs(obs_id: str = "obs_001", user_id: str = "alice",
             session_id: str = "s1", turn_id: str = "t0",
             **overrides) -> L1Observation:
    base = {
        "obs_id": obs_id,
        "user_id": user_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "created_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "raw_text": "The system is running.",
        "language": "en",
        "cms_real": [0.5, 0.7, 0.6],
        "cms_imag": [0.3, 0.4, 0.5],
        "temporal_phase": 1.57,
        "features": {"semantic_density": 0.5, "pragmatic_load": 0.3},
        "tags": ["technical"],
        "entities": ["server"],
        "quality": {"text_length": 22.0},
        "metadata": {"source": "test"},
    }
    base.update(overrides)
    return L1Observation(**base)


class TestSaveAndGet:
    def test_save_and_retrieve_roundtrip(self, store):
        obs = make_obs()
        store.save(obs)

        retrieved = store.get("obs_001")
        assert retrieved is not None
        assert retrieved.obs_id == obs.obs_id
        assert retrieved.user_id == obs.user_id
        assert retrieved.raw_text == obs.raw_text
        assert retrieved.cms_real == obs.cms_real
        assert retrieved.cms_imag == obs.cms_imag
        assert retrieved.features == obs.features
        assert retrieved.tags == obs.tags
        assert retrieved.metadata == obs.metadata

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("does_not_exist") is None

    def test_save_replaces_existing(self, store):
        obs1 = make_obs(raw_text="version 1")
        obs2 = make_obs(raw_text="version 2")
        store.save(obs1)
        store.save(obs2)

        retrieved = store.get("obs_001")
        assert retrieved.raw_text == "version 2"

    def test_datetime_preserves_timezone(self, store):
        obs = make_obs()
        store.save(obs)
        retrieved = store.get("obs_001")
        assert retrieved.created_at.tzinfo is not None
        assert retrieved.created_at == obs.created_at

    def test_null_language_preserved(self, store):
        obs = make_obs(language=None)
        store.save(obs)
        retrieved = store.get("obs_001")
        assert retrieved.language is None


class TestBulkSave:
    def test_save_many_returns_count(self, store):
        obs_list = [make_obs(obs_id=f"obs_{i:03d}") for i in range(5)]
        count = store.save_many(obs_list)
        assert count == 5

    def test_save_many_persists_all(self, store):
        obs_list = [make_obs(obs_id=f"obs_{i:03d}") for i in range(3)]
        store.save_many(obs_list)

        for i in range(3):
            assert store.get(f"obs_{i:03d}") is not None


class TestListing:
    def test_list_for_session(self, store):
        for i in range(3):
            store.save(make_obs(
                obs_id=f"obs_{i}",
                session_id="session_a",
                created_at=datetime(2026, 1, 1, 12, i, 0, tzinfo=timezone.utc),
            ))
        store.save(make_obs(obs_id="other", session_id="session_b"))

        results = store.list_for_session("alice", "session_a")
        assert len(results) == 3
        assert all(r.session_id == "session_a" for r in results)

    def test_list_for_session_ordered_by_time(self, store):
        # Insert in reverse time order
        for i in [2, 0, 1]:
            store.save(make_obs(
                obs_id=f"obs_{i}",
                created_at=datetime(2026, 1, 1, 12, i, 0, tzinfo=timezone.utc),
            ))
        results = store.list_for_session("alice", "s1")
        timestamps = [r.created_at for r in results]
        assert timestamps == sorted(timestamps)

    def test_list_for_user_filters(self, store):
        store.save(make_obs(obs_id="a", user_id="alice"))
        store.save(make_obs(obs_id="b", user_id="bob"))
        store.save(make_obs(obs_id="c", user_id="alice"))

        alice_obs = store.list_for_user("alice")
        bob_obs = store.list_for_user("bob")
        assert len(alice_obs) == 2
        assert len(bob_obs) == 1

    def test_list_respects_limit(self, store):
        for i in range(10):
            store.save(make_obs(obs_id=f"obs_{i}"))
        results = store.list_for_user("alice", limit=3)
        assert len(results) == 3


class TestCount:
    def test_count_for_user(self, store):
        for i in range(5):
            store.save(make_obs(obs_id=f"obs_{i}", user_id="alice"))
        store.save(make_obs(obs_id="bob_obs", user_id="bob"))
        assert store.count_for_user("alice") == 5
        assert store.count_for_user("bob") == 1
        assert store.count_for_user("nobody") == 0


class TestDelete:
    def test_delete_single(self, store):
        store.save(make_obs())
        assert store.get("obs_001") is not None
        store.delete("obs_001")
        assert store.get("obs_001") is None

    def test_delete_for_user(self, store):
        for i in range(3):
            store.save(make_obs(obs_id=f"a_{i}", user_id="alice"))
        store.save(make_obs(obs_id="bob_obs", user_id="bob"))

        deleted_count = store.delete_for_user("alice")
        assert deleted_count == 3
        assert store.count_for_user("alice") == 0
        assert store.count_for_user("bob") == 1


class TestSerialization:
    def test_unicode_preserved(self, store):
        obs = make_obs(raw_text="Le serveur fonctionne — c'est parfait.")
        store.save(obs)
        retrieved = store.get("obs_001")
        assert retrieved.raw_text == obs.raw_text

    def test_empty_collections_roundtrip(self, store):
        obs = make_obs(features={}, tags=[], entities=[], quality={}, metadata={})
        store.save(obs)
        retrieved = store.get("obs_001")
        assert retrieved.features == {}
        assert retrieved.tags == []
        assert retrieved.metadata == {}

    def test_nested_metadata_roundtrip(self, store):
        nested = {"source": {"system": "test", "version": 2}, "flags": [1, 2, 3]}
        obs = make_obs(metadata=nested)
        store.save(obs)
        retrieved = store.get("obs_001")
        assert retrieved.metadata == nested
