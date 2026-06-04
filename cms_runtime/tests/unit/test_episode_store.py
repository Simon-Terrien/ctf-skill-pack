"""Unit tests for EpisodeStore CRUD."""

from datetime import datetime, timedelta, timezone

import pytest

from cms.l2.episode import L2Episode
from cms.storage.episode_store import EpisodeStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


@pytest.fixture
def backend():
    be = SQLiteBackend(":memory:")
    be.bootstrap_schema(FULL_SCHEMA_DDL)
    yield be
    be.close()


@pytest.fixture
def store(backend):
    return EpisodeStore(backend)


def make_episode(episode_id="ep_001", user_id="alice", session_id="s1",
                 start_offset_seconds=0, **overrides) -> L2Episode:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    start = base + timedelta(seconds=start_offset_seconds)
    kwargs = {
        "episode_id": episode_id,
        "user_id": user_id,
        "session_id": session_id,
        "created_at": base,
        "start_at": start,
        "end_at": start + timedelta(seconds=60),
        "obs_ids": ["obs_0", "obs_1", "obs_2"],
        "trajectory_signature": {"mean": 0.5, "std": 0.1},
        "surprise_score": 1.0,
        "drift_score": 0.5,
        "confidence_score": 0.8,
        "closure_reason": "window_full",
        "metadata": {"source": "test"},
    }
    kwargs.update(overrides)
    return L2Episode(**kwargs)


class TestSaveAndGet:
    def test_save_and_retrieve(self, store):
        ep = make_episode()
        store.save(ep)
        retrieved = store.get("ep_001")

        assert retrieved is not None
        assert retrieved.episode_id == ep.episode_id
        assert retrieved.obs_ids == ep.obs_ids
        assert retrieved.trajectory_signature == ep.trajectory_signature
        assert retrieved.closure_reason == ep.closure_reason

    def test_get_nonexistent(self, store):
        assert store.get("missing") is None

    def test_save_replaces_existing(self, store):
        ep1 = make_episode(closure_reason="window_full")
        ep2 = make_episode(closure_reason="surprise_spike")
        store.save(ep1)
        store.save(ep2)
        assert store.get("ep_001").closure_reason == "surprise_spike"

    def test_datetime_preserves_timezone(self, store):
        ep = make_episode()
        store.save(ep)
        retrieved = store.get("ep_001")
        assert retrieved.start_at.tzinfo is not None
        assert retrieved.start_at == ep.start_at
        assert retrieved.end_at == ep.end_at


class TestBulkSave:
    def test_save_many(self, store):
        episodes = [make_episode(episode_id=f"ep_{i}") for i in range(5)]
        count = store.save_many(episodes)
        assert count == 5
        for i in range(5):
            assert store.get(f"ep_{i}") is not None


class TestListing:
    def test_list_for_session_ordered_by_start(self, store):
        # Insert in shuffled order
        for offset in [60, 0, 120, 30]:
            store.save(make_episode(
                episode_id=f"ep_{offset}",
                start_offset_seconds=offset,
            ))
        results = store.list_for_session("alice", "s1")
        offsets = [e.episode_id for e in results]
        assert offsets == ["ep_0", "ep_30", "ep_60", "ep_120"]

    def test_list_for_session_filters(self, store):
        store.save(make_episode(episode_id="a", session_id="s1"))
        store.save(make_episode(episode_id="b", session_id="s2"))
        store.save(make_episode(episode_id="c", session_id="s1"))

        s1_eps = store.list_for_session("alice", "s1")
        assert len(s1_eps) == 2

    def test_list_for_user(self, store):
        store.save(make_episode(episode_id="a", user_id="alice"))
        store.save(make_episode(episode_id="b", user_id="bob"))
        store.save(make_episode(episode_id="c", user_id="alice"))

        alice_eps = store.list_for_user("alice")
        bob_eps = store.list_for_user("bob")
        assert len(alice_eps) == 2
        assert len(bob_eps) == 1


class TestRangeQueries:
    def test_list_in_range_filters(self, store):
        # Episodes at offsets 0, 30, 60, 120 seconds
        for offset in [0, 30, 60, 120]:
            store.save(make_episode(
                episode_id=f"ep_{offset}",
                start_offset_seconds=offset,
            ))

        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Range covering 25-65 seconds
        results = store.list_in_range(
            "alice",
            start=base + timedelta(seconds=25),
            end=base + timedelta(seconds=65),
        )
        assert len(results) == 2
        assert {e.episode_id for e in results} == {"ep_30", "ep_60"}


class TestCount:
    def test_count_for_user(self, store):
        for i in range(3):
            store.save(make_episode(episode_id=f"a_{i}", user_id="alice"))
        store.save(make_episode(episode_id="b", user_id="bob"))
        assert store.count_for_user("alice") == 3
        assert store.count_for_user("bob") == 1
        assert store.count_for_user("nobody") == 0


class TestDelete:
    def test_delete_single(self, store):
        store.save(make_episode())
        store.delete("ep_001")
        assert store.get("ep_001") is None

    def test_delete_for_user(self, store):
        for i in range(3):
            store.save(make_episode(episode_id=f"a_{i}", user_id="alice"))
        store.save(make_episode(episode_id="b", user_id="bob"))
        deleted = store.delete_for_user("alice")
        assert deleted == 3
        assert store.count_for_user("alice") == 0
        assert store.count_for_user("bob") == 1


class TestSerialization:
    def test_obs_ids_roundtrip_preserves_order(self, store):
        ep = make_episode(obs_ids=["z", "a", "m", "b"])
        store.save(ep)
        retrieved = store.get("ep_001")
        assert retrieved.obs_ids == ["z", "a", "m", "b"]

    def test_complex_signature_roundtrip(self, store):
        sig = {
            "mean_velocity": 0.42,
            "fractal_dim": 1.85,
            "lyap_exp": -0.003,
        }
        ep = make_episode(trajectory_signature=sig)
        store.save(ep)
        assert store.get("ep_001").trajectory_signature == sig

    def test_unicode_in_metadata(self, store):
        ep = make_episode(metadata={"note": "épisode → fonctionne"})
        store.save(ep)
        assert store.get("ep_001").metadata["note"] == "épisode → fonctionne"
