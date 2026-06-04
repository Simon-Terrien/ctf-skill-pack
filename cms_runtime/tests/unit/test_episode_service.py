"""Unit tests for EpisodeService — closure mechanics and multi-session isolation."""

from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from cms.l1.observation import L1Observation
from cms.l2.policies import (
    EuclideanSurpriseScorer,
    SurpriseClosurePolicy,
    WindowedClosurePolicy,
)
from cms.l2.service import EpisodeService
from cms.storage.episode_store import EpisodeStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


# ── fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def store():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    yield EpisodeStore(backend)
    backend.close()


@pytest.fixture
def fixed_clock():
    return lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sequential_ids():
    counter = count(0)
    return lambda: f"ep_{next(counter):03d}"


def make_obs(obs_id: str, user_id: str = "alice", session_id: str = "s1",
             t_seconds: int = 0, cms_real=None, cms_imag=None) -> L1Observation:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return L1Observation(
        obs_id=obs_id,
        user_id=user_id,
        session_id=session_id,
        turn_id=f"t_{obs_id}",
        created_at=base + timedelta(seconds=t_seconds),
        raw_text=f"utterance {obs_id}",
        language="en",
        cms_real=cms_real or [0.5, 0.5, 0.5],
        cms_imag=cms_imag or [0.3, 0.3, 0.3],
        temporal_phase=0.0,
    )


# ── basic closure behavior ───────────────────────────────────────────

class TestWindowedClosure:
    def test_no_close_below_window(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=5),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        for i in range(3):
            closed = service.update(make_obs(f"o_{i}", t_seconds=i))
            assert closed is None
        assert service.open_size("alice", "s1") == 3

    def test_closes_at_window_boundary(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=5),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        # Fill to 4 observations
        for i in range(4):
            closed = service.update(make_obs(f"o_{i}", t_seconds=i))
            assert closed is None

        # 5th triggers close — should contain the 4 accumulated
        closed = service.update(make_obs("o_4", t_seconds=4))
        assert closed is not None
        assert closed.length == 4
        assert closed.obs_ids == ["o_0", "o_1", "o_2", "o_3"]
        assert closed.closure_reason == "window_full"

    def test_triggering_obs_starts_new_episode(self, store, fixed_clock, sequential_ids):
        """Key semantic: the observation that triggers close goes to the NEW episode."""
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=5),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        for i in range(4):
            service.update(make_obs(f"o_{i}", t_seconds=i))
        closed = service.update(make_obs("o_4", t_seconds=4))

        # Closed episode contains o_0..o_3
        assert "o_4" not in closed.obs_ids
        # Open episode now contains o_4
        assert service.open_size("alice", "s1") == 1

    def test_consecutive_episodes(self, store, fixed_clock, sequential_ids):
        """Run enough observations to produce multiple episodes."""
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=3),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        closed_episodes = []
        for i in range(10):
            result = service.update(make_obs(f"o_{i}", t_seconds=i))
            if result is not None:
                closed_episodes.append(result)

        # 10 observations, max_size=3 → close at 3rd, 5th, 7th, 9th = 4 closures
        # Actually: fills 1,2 → 3rd closes (contains 2 obs), etc.
        # Pattern: o_0,o_1 then o_2 triggers close with [o_0,o_1], o_2 starts new...
        assert len(closed_episodes) >= 3
        # Verify no observation id appears in two episodes
        all_obs_ids = [oid for ep in closed_episodes for oid in ep.obs_ids]
        assert len(all_obs_ids) == len(set(all_obs_ids))


class TestSurpriseClosure:
    def test_surprise_triggers_close(self, store, fixed_clock, sequential_ids):
        scorer = EuclideanSurpriseScorer()
        service = EpisodeService(
            store=store,
            policy=SurpriseClosurePolicy(scorer, threshold=2.0, min_history=3),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        # Build a stable baseline with mild variance so std > 0
        base_obs = [make_obs(f"o_{i}", t_seconds=i) for i in range(8)]
        base_obs[2] = make_obs("o_2", t_seconds=2,
                                cms_real=[0.51, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3])
        for obs in base_obs:
            closed = service.update(obs)
            assert closed is None

        # Outlier: should trigger close
        outlier = make_obs("o_out", t_seconds=10,
                           cms_real=[0.95, 0.95, 0.95], cms_imag=[0.95, 0.95, 0.95])
        closed = service.update(outlier)
        assert closed is not None
        assert "surprise_spike" in closed.closure_reason
        # Closed episode contains the 8 baseline observations
        assert closed.length == 8
        assert "o_out" not in closed.obs_ids


# ── persistence ──────────────────────────────────────────────────────

class TestPersistence:
    def test_closed_episode_persisted_to_store(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=3),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        for i in range(3):
            closed = service.update(make_obs(f"o_{i}", t_seconds=i))
        assert closed is not None

        # Verify persisted
        retrieved = store.get(closed.episode_id)
        assert retrieved is not None
        assert retrieved.obs_ids == closed.obs_ids

    def test_timestamps_reflect_observation_range(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=3),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        service.update(make_obs("o_0", t_seconds=0))
        service.update(make_obs("o_1", t_seconds=10))
        closed = service.update(make_obs("o_2", t_seconds=20))

        # Episode start = first obs time, end = last obs time in the *closed* episode
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert closed.start_at == base + timedelta(seconds=0)
        assert closed.end_at == base + timedelta(seconds=10)

    def test_episode_ids_unique(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=2),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        seen = set()
        for i in range(10):
            closed = service.update(make_obs(f"o_{i}", t_seconds=i))
            if closed:
                assert closed.episode_id not in seen
                seen.add(closed.episode_id)


# ── multi-session isolation ──────────────────────────────────────────

class TestMultiSessionIsolation:
    def test_sessions_tracked_independently(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=5),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        # Interleave observations across two sessions
        service.update(make_obs("a_0", session_id="s1"))
        service.update(make_obs("b_0", session_id="s2"))
        service.update(make_obs("a_1", session_id="s1"))
        service.update(make_obs("b_1", session_id="s2"))

        assert service.open_size("alice", "s1") == 2
        assert service.open_size("alice", "s2") == 2

    def test_closure_in_one_session_does_not_affect_other(
        self, store, fixed_clock, sequential_ids
    ):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=3),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        # Fill s1 to trigger close
        for i in range(3):
            service.update(make_obs(f"a_{i}", session_id="s1"))
        # Add to s2
        service.update(make_obs("b_0", session_id="s2"))

        # s1 was closed and restarted with the triggering obs
        assert service.open_size("alice", "s1") == 1
        # s2 untouched
        assert service.open_size("alice", "s2") == 1

    def test_users_isolated(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=5),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        service.update(make_obs("a", user_id="alice"))
        service.update(make_obs("b", user_id="bob"))
        service.update(make_obs("c", user_id="alice"))

        assert service.open_size("alice", "s1") == 2
        assert service.open_size("bob", "s1") == 1


# ── flush and reset ──────────────────────────────────────────────────

class TestFlush:
    def test_flush_persists_open_episode(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=100),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        for i in range(4):
            service.update(make_obs(f"o_{i}", t_seconds=i))

        closed = service.flush("alice", "s1")
        assert closed is not None
        assert closed.length == 4
        assert closed.closure_reason == "flush"
        assert service.open_size("alice", "s1") == 0
        # Verify persisted
        assert store.get(closed.episode_id) is not None

    def test_flush_with_no_open_episode(self, store, fixed_clock, sequential_ids):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=100),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        assert service.flush("alice", "s1") is None

    def test_flush_does_not_affect_other_sessions(
        self, store, fixed_clock, sequential_ids
    ):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=100),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        service.update(make_obs("a_0", session_id="s1"))
        service.update(make_obs("b_0", session_id="s2"))

        service.flush("alice", "s1")
        assert service.open_size("alice", "s1") == 0
        assert service.open_size("alice", "s2") == 1


class TestReset:
    def test_reset_drops_open_without_persisting(
        self, store, fixed_clock, sequential_ids
    ):
        service = EpisodeService(
            store=store,
            policy=WindowedClosurePolicy(max_size=100),
            clock=fixed_clock,
            id_factory=sequential_ids,
        )
        for i in range(4):
            service.update(make_obs(f"o_{i}"))

        assert service.open_size("alice", "s1") == 4
        count_before = store.count_for_user("alice")
        service.reset("alice", "s1")
        assert service.open_size("alice", "s1") == 0
        # Not persisted
        assert store.count_for_user("alice") == count_before
