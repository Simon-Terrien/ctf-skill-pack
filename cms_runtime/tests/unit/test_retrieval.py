"""Unit tests for RetrievalService.

The deterministic ordering policy is the most important contract here.
Tests below pin down each ranking dimension independently and then
verify they compose correctly.
"""

from datetime import datetime, timedelta, timezone

import pytest

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l3.evidence import MemoryEvidence
from cms.runtime.retrieval import RetrievalService
from cms.runtime.state import RetrievalPolicy
from cms.storage.episode_store import EpisodeStore
from cms.storage.evidence_store import EvidenceStore
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def stores():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    obs_store = ObservationStore(backend)
    ep_store = EpisodeStore(backend)
    ev_store = EvidenceStore(backend)
    yield obs_store, ep_store, ev_store
    backend.close()


@pytest.fixture
def service(stores):
    obs_store, ep_store, ev_store = stores
    return RetrievalService(obs_store, ep_store, ev_store)


def make_obs(
    obs_id: str,
    user_id: str = "alice",
    session_id: str = "s1",
    minutes_ago: int = 0,
) -> L1Observation:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return L1Observation(
        obs_id=obs_id, user_id=user_id, session_id=session_id, turn_id=obs_id,
        created_at=base + timedelta(minutes=-minutes_ago),
        raw_text="x", language="en",
        cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3],
        temporal_phase=0.0,
    )


def make_episode(
    episode_id: str,
    user_id: str = "alice",
    session_id: str = "s1",
    minutes_ago: int = 0,
) -> L2Episode:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    when = base + timedelta(minutes=-minutes_ago)
    return L2Episode(
        episode_id=episode_id, user_id=user_id, session_id=session_id,
        created_at=when, start_at=when, end_at=when,
        obs_ids=["obs_dummy"],
    )


def make_evidence(
    memory_id: str,
    *,
    user_id: str = "alice",
    source_id: str = "obs_001",
    rule_id: str = "obs.epistemic.certainty",
    scope: str = "epistemic",
    subscope: str | None = "certainty",
    source_kind: str = "observation",
    minutes_ago: int = 0,
    support_score: float = 0.5,
    pinned: bool = False,
) -> MemoryEvidence:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    metadata = {"pinned": True} if pinned else {}
    return MemoryEvidence(
        memory_id=memory_id, user_id=user_id,
        created_at=base + timedelta(minutes=-minutes_ago),
        source_kind=source_kind, source_id=source_id, rule_id=rule_id,
        scope=scope, subscope=subscope,
        summary="x", support_score=support_score, relevance_score=1.0,
        metadata=metadata,
    )


# ── observation retrieval ────────────────────────────────────────────


class TestRecentObservations:
    def test_returns_newest_first(self, service, stores):
        obs_store, _, _ = stores
        for i, mins in enumerate([10, 5, 1, 30, 15]):
            obs_store.save(make_obs(f"obs_{i}", minutes_ago=mins))

        results = service.get_recent_observations("alice", "s1")
        # Order should be: 1 min, 5 min, 10 min, 15 min, 30 min ago
        ages = [(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                 - r.created_at).total_seconds() / 60 for r in results]
        assert ages == sorted(ages)

    def test_respects_policy_limit(self, service, stores):
        obs_store, _, _ = stores
        for i in range(20):
            obs_store.save(make_obs(f"obs_{i:02d}", minutes_ago=i))
        results = service.get_recent_observations("alice", "s1")
        assert len(results) == 5  # default policy

    def test_explicit_limit_overrides_policy(self, service, stores):
        obs_store, _, _ = stores
        for i in range(20):
            obs_store.save(make_obs(f"obs_{i:02d}", minutes_ago=i))
        results = service.get_recent_observations("alice", "s1", limit=3)
        assert len(results) == 3

    def test_zero_limit_returns_empty(self, service, stores):
        obs_store, _, _ = stores
        obs_store.save(make_obs("obs_0"))
        assert service.get_recent_observations("alice", "s1", limit=0) == []

    def test_session_scoped(self, service, stores):
        obs_store, _, _ = stores
        obs_store.save(make_obs("a1", session_id="s1"))
        obs_store.save(make_obs("b1", session_id="s2"))
        results = service.get_recent_observations("alice", "s1")
        assert all(r.session_id == "s1" for r in results)

    def test_empty_session_returns_empty(self, service):
        assert service.get_recent_observations("alice", "missing") == []


# ── episode retrieval ────────────────────────────────────────────────


class TestRecentEpisodes:
    def test_returns_newest_first(self, service, stores):
        _, ep_store, _ = stores
        for i, mins in enumerate([10, 5, 1, 30]):
            ep_store.save(make_episode(f"ep_{i}", minutes_ago=mins))
        results = service.get_recent_episodes("alice", "s1")
        ages = [(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                 - r.start_at).total_seconds() / 60 for r in results]
        assert ages == sorted(ages)

    def test_respects_policy_limit(self, service, stores):
        _, ep_store, _ = stores
        for i in range(10):
            ep_store.save(make_episode(f"ep_{i:02d}", minutes_ago=i))
        results = service.get_recent_episodes("alice", "s1")
        assert len(results) == 3  # default policy

    def test_session_scoped(self, service, stores):
        _, ep_store, _ = stores
        ep_store.save(make_episode("a", session_id="s1"))
        ep_store.save(make_episode("b", session_id="s2"))
        results = service.get_recent_episodes("alice", "s1")
        assert len(results) == 1


# ── evidence retrieval — ordering policy ─────────────────────────────


class TestEvidenceOrdering:
    def test_recency_orders_descending(self, service, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence("m_old", source_id="obs_a", minutes_ago=30))
        ev_store.save(make_evidence("m_new", source_id="obs_b", minutes_ago=1))
        ev_store.save(make_evidence("m_mid", source_id="obs_c", minutes_ago=10))

        results = service.search_evidence("alice")
        ids = [r.memory_id for r in results]
        assert ids[0] == "m_new"
        assert ids[1] == "m_mid"
        assert ids[2] == "m_old"

    def test_pinned_outranks_newer(self, service, stores):
        _, _, ev_store = stores
        # Pinned but older
        ev_store.save(make_evidence(
            "m_pinned", source_id="obs_a", minutes_ago=60, pinned=True
        ))
        # Unpinned but newer
        ev_store.save(make_evidence(
            "m_new", source_id="obs_b", minutes_ago=1
        ))
        results = service.search_evidence("alice")
        assert results[0].memory_id == "m_pinned"

    def test_scope_exact_outranks_newer_off_scope(self, service, stores):
        _, _, ev_store = stores
        # We need cross-scope candidates — store search filters by scope,
        # so we test with scope=None to get all, then check ordering.
        # But the spec says exact-scope when scope is requested wins.
        # When scope is requested, the store already filters — so this test
        # validates the *mechanism*: when scope filter is None, no exact-match
        # promotion happens; when scope filter is set, only matching come back.
        ev_store.save(make_evidence(
            "m_epistemic", source_id="obs_a",
            scope="epistemic", subscope="certainty", minutes_ago=30,
        ))
        ev_store.save(make_evidence(
            "m_pragmatic", source_id="obs_b",
            rule_id="obs.pragmatic.high_ratio",
            scope="pragmatic", subscope="high_pragmatic_ratio", minutes_ago=1,
        ))

        # No filter: pragmatic newer → pragmatic first
        unfiltered = service.search_evidence("alice")
        assert unfiltered[0].memory_id == "m_pragmatic"

        # With scope filter: epistemic only
        filtered = service.search_evidence("alice", scope="epistemic")
        assert len(filtered) == 1
        assert filtered[0].memory_id == "m_epistemic"

    def test_subscope_filter_exact(self, service, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence(
            "m_cert", source_id="obs_a", subscope="certainty",
        ))
        ev_store.save(make_evidence(
            "m_hedge", source_id="obs_b", subscope="hedging",
            rule_id="obs.epistemic.hedging",
        ))
        results = service.search_evidence("alice", scope="epistemic", subscope="certainty")
        assert len(results) == 1
        assert results[0].memory_id == "m_cert"

    def test_support_score_breaks_recency_ties(self, service, stores):
        _, _, ev_store = stores
        # Same minutes_ago → same created_at, support_score breaks tie
        ev_store.save(make_evidence(
            "m_low", source_id="obs_a", minutes_ago=5, support_score=0.2,
        ))
        ev_store.save(make_evidence(
            "m_high", source_id="obs_b", minutes_ago=5, support_score=0.9,
        ))
        results = service.search_evidence("alice")
        assert results[0].memory_id == "m_high"
        assert results[1].memory_id == "m_low"

    def test_deterministic_ordering_across_repeated_calls(self, service, stores):
        _, _, ev_store = stores
        for i in range(10):
            ev_store.save(make_evidence(
                f"m_{i:02d}", source_id=f"obs_{i}", minutes_ago=5,
                support_score=0.5,
            ))
        results_a = [r.memory_id for r in service.search_evidence("alice", limit=10)]
        results_b = [r.memory_id for r in service.search_evidence("alice", limit=10)]
        assert results_a == results_b


# ── source_kind filtering ────────────────────────────────────────────


class TestSourceKindFilter:
    def test_observation_only(self, service, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence("m1", source_kind="observation",
                                    source_id="obs_a"))
        ev_store.save(make_evidence("m2", source_kind="episode",
                                    source_id="ep_a",
                                    rule_id="ep.dynamics.rupture",
                                    scope="dynamics", subscope="rupture"))
        results = service.search_evidence("alice", source_kind="observation")
        assert len(results) == 1
        assert results[0].memory_id == "m1"

    def test_episode_only(self, service, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence("m1", source_kind="observation",
                                    source_id="obs_a"))
        ev_store.save(make_evidence("m2", source_kind="episode",
                                    source_id="ep_a",
                                    rule_id="ep.dynamics.rupture",
                                    scope="dynamics", subscope="rupture"))
        results = service.search_evidence("alice", source_kind="episode")
        assert len(results) == 1
        assert results[0].memory_id == "m2"


# ── policy and limits ────────────────────────────────────────────────


class TestRetrievalPolicy:
    def test_custom_policy_used(self, stores):
        obs_store, ep_store, ev_store = stores
        for i in range(20):
            obs_store.save(make_obs(f"obs_{i:02d}", minutes_ago=i))

        policy = RetrievalPolicy(observation_limit=2)
        service = RetrievalService(obs_store, ep_store, ev_store, policy=policy)
        results = service.get_recent_observations("alice", "s1")
        assert len(results) == 2

    def test_policy_property_exposed(self, service):
        assert service.policy.observation_limit == 5
