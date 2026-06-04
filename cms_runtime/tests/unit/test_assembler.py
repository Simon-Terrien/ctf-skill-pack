"""Unit tests for StateAssembler.

Covers:
  - canonical view composition from persisted state
  - empty state handled cleanly
  - signals (numeric ages) computed correctly
  - counts populated
  - freshness flags reflect threshold logic
  - scope/subscope filters narrow evidence retrieval
  - ids in the view come from real persisted records (no fabrication)
"""

from datetime import datetime, timedelta, timezone

import pytest

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l3.evidence import MemoryEvidence
from cms.runtime.assembler import StateAssembler
from cms.runtime.retrieval import RetrievalService
from cms.runtime.state import RetrievalPolicy
from cms.storage.episode_store import EpisodeStore
from cms.storage.evidence_store import EvidenceStore
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


# ── fixtures ─────────────────────────────────────────────────────────


FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def stores():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    yield (
        ObservationStore(backend),
        EpisodeStore(backend),
        EvidenceStore(backend),
    )
    backend.close()


@pytest.fixture
def assembler(stores):
    obs_store, ep_store, ev_store = stores
    retrieval = RetrievalService(obs_store, ep_store, ev_store)
    return StateAssembler(retrieval, clock=lambda: FIXED_NOW)


def make_obs(obs_id: str, *, minutes_ago: int = 0,
             user_id: str = "alice", session_id: str = "s1") -> L1Observation:
    return L1Observation(
        obs_id=obs_id, user_id=user_id, session_id=session_id, turn_id=obs_id,
        created_at=FIXED_NOW - timedelta(minutes=minutes_ago),
        raw_text="x", language="en",
        cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3],
        temporal_phase=0.0,
    )


def make_episode(episode_id: str, *, minutes_ago: int = 0,
                 user_id: str = "alice", session_id: str = "s1") -> L2Episode:
    when = FIXED_NOW - timedelta(minutes=minutes_ago)
    return L2Episode(
        episode_id=episode_id, user_id=user_id, session_id=session_id,
        created_at=when, start_at=when, end_at=when,
        obs_ids=["obs_dummy"],
    )


def make_evidence(memory_id: str, *, minutes_ago: int = 0,
                  source_id: str = "obs_001",
                  scope: str = "epistemic", subscope: str = "certainty",
                  rule_id: str = "obs.epistemic.certainty") -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=memory_id, user_id="alice",
        created_at=FIXED_NOW - timedelta(minutes=minutes_ago),
        source_kind="observation", source_id=source_id, rule_id=rule_id,
        scope=scope, subscope=subscope,
        summary="x", support_score=0.5, relevance_score=1.0,
    )


# ── empty state ──────────────────────────────────────────────────────


class TestEmptyState:
    def test_empty_user_session_yields_clean_view(self, assembler):
        view = assembler.build("nobody", "nowhere")
        assert view.user_id == "nobody"
        assert view.session_id == "nowhere"
        assert view.current_observation is None
        assert view.recent_observations == []
        assert view.recent_episodes == []
        assert view.retrieved_evidence == []
        assert view.signals == {}

    def test_empty_state_freshness_flags_all_false(self, assembler):
        view = assembler.build("nobody", "nowhere")
        assert view.freshness_flags["has_recent_observations"] is False
        assert view.freshness_flags["has_recent_episodes"] is False
        assert view.freshness_flags["has_recent_evidence"] is False

    def test_empty_state_counts_all_zero(self, assembler):
        view = assembler.build("nobody", "nowhere")
        assert view.counts["recent_observations"] == 0
        assert view.counts["recent_episodes"] == 0
        assert view.counts["retrieved_evidence"] == 0


# ── populated state ──────────────────────────────────────────────────


class TestPopulatedState:
    def test_current_observation_is_newest(self, assembler, stores):
        obs_store, _, _ = stores
        obs_store.save(make_obs("o_old", minutes_ago=10))
        obs_store.save(make_obs("o_new", minutes_ago=1))
        view = assembler.build("alice", "s1")
        assert view.current_observation is not None
        assert view.current_observation.obs_id == "o_new"

    def test_recent_observations_ordered_newest_first(self, assembler, stores):
        obs_store, _, _ = stores
        for i in range(3):
            obs_store.save(make_obs(f"o_{i}", minutes_ago=i * 5))
        view = assembler.build("alice", "s1")
        ages = [(FIXED_NOW - o.created_at).total_seconds() for o in view.recent_observations]
        assert ages == sorted(ages)

    def test_view_carries_real_persisted_records(self, assembler, stores):
        obs_store, ep_store, ev_store = stores
        obs_store.save(make_obs("o1"))
        ep_store.save(make_episode("e1"))
        ev_store.save(make_evidence("m1"))

        view = assembler.build("alice", "s1")
        # Embedded records carry their own ids — no fabrication
        assert view.current_observation_id == "o1"
        assert "e1" in view.recent_episode_ids
        assert "m1" in view.retrieved_evidence_ids


# ── signals (numeric ages) ───────────────────────────────────────────


class TestSignals:
    def test_observation_age_computed(self, assembler, stores):
        obs_store, _, _ = stores
        obs_store.save(make_obs("o1", minutes_ago=2))  # 120 seconds
        view = assembler.build("alice", "s1")
        assert view.signals["latest_observation_age_seconds"] == 120.0

    def test_episode_age_computed(self, assembler, stores):
        _, ep_store, _ = stores
        ep_store.save(make_episode("e1", minutes_ago=5))  # 300 seconds
        view = assembler.build("alice", "s1")
        assert view.signals["latest_episode_age_seconds"] == 300.0

    def test_evidence_age_computed(self, assembler, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence("m1", minutes_ago=1))  # 60 seconds
        view = assembler.build("alice", "s1")
        assert view.signals["latest_evidence_age_seconds"] == 60.0

    def test_no_signal_when_no_data(self, assembler, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence("m1"))  # only evidence, no obs/episodes
        view = assembler.build("alice", "s1")
        assert "latest_observation_age_seconds" not in view.signals
        assert "latest_episode_age_seconds" not in view.signals
        assert "latest_evidence_age_seconds" in view.signals


# ── freshness flags ──────────────────────────────────────────────────


class TestFreshnessFlags:
    def test_recent_obs_within_threshold_is_fresh(self, assembler, stores):
        obs_store, _, _ = stores
        obs_store.save(make_obs("o1", minutes_ago=2))  # under 5min default
        view = assembler.build("alice", "s1")
        assert view.freshness_flags["has_recent_observations"] is True

    def test_old_obs_beyond_threshold_is_stale(self, assembler, stores):
        obs_store, _, _ = stores
        obs_store.save(make_obs("o1", minutes_ago=60))  # over 5min default
        view = assembler.build("alice", "s1")
        assert view.freshness_flags["has_recent_observations"] is False

    def test_custom_thresholds_respected(self, stores):
        obs_store, ep_store, ev_store = stores
        obs_store.save(make_obs("o1", minutes_ago=10))  # 600s

        retrieval = RetrievalService(obs_store, ep_store, ev_store)
        # Tight threshold: 60s
        tight = StateAssembler(
            retrieval, clock=lambda: FIXED_NOW,
            recent_observation_seconds=60.0,
        )
        # Loose threshold: 1 hour
        loose = StateAssembler(
            retrieval, clock=lambda: FIXED_NOW,
            recent_observation_seconds=3600.0,
        )
        assert tight.build("alice", "s1").freshness_flags["has_recent_observations"] is False
        assert loose.build("alice", "s1").freshness_flags["has_recent_observations"] is True

    def test_boundary_age_is_fresh(self, stores):
        """Exactly at the threshold counts as fresh (≤, not <)."""
        obs_store, ep_store, ev_store = stores
        obs_store.save(make_obs("o1", minutes_ago=5))  # exactly 300s
        retrieval = RetrievalService(obs_store, ep_store, ev_store)
        assembler = StateAssembler(
            retrieval, clock=lambda: FIXED_NOW,
            recent_observation_seconds=300.0,
        )
        assert assembler.build("alice", "s1").freshness_flags["has_recent_observations"] is True


# ── scope filtering ──────────────────────────────────────────────────


class TestScopeFiltering:
    def test_scope_narrows_evidence(self, assembler, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence("m1", scope="epistemic", subscope="certainty"))
        ev_store.save(make_evidence(
            "m2", source_id="obs_002",
            scope="pragmatic", subscope="high_pragmatic_ratio",
            rule_id="obs.pragmatic.high_ratio",
        ))
        view = assembler.build("alice", "s1", scope="epistemic")
        ids = view.retrieved_evidence_ids
        assert "m1" in ids
        assert "m2" not in ids

    def test_subscope_narrows_further(self, assembler, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence("m_cert", scope="epistemic", subscope="certainty"))
        ev_store.save(make_evidence(
            "m_hedge", source_id="obs_002",
            scope="epistemic", subscope="hedging",
            rule_id="obs.epistemic.hedging",
        ))
        view = assembler.build("alice", "s1", scope="epistemic", subscope="certainty")
        assert view.retrieved_evidence_ids == ["m_cert"]

    def test_no_scope_filter_returns_all(self, assembler, stores):
        _, _, ev_store = stores
        ev_store.save(make_evidence("m1"))
        ev_store.save(make_evidence(
            "m2", source_id="obs_002",
            scope="pragmatic", subscope="high_pragmatic_ratio",
            rule_id="obs.pragmatic.high_ratio",
        ))
        view = assembler.build("alice", "s1")
        assert len(view.retrieved_evidence_ids) == 2


# ── determinism ──────────────────────────────────────────────────────


class TestDeterminism:
    def test_repeated_build_yields_identical_view(self, assembler, stores):
        obs_store, ep_store, ev_store = stores
        for i in range(3):
            obs_store.save(make_obs(f"o_{i}", minutes_ago=i))
            ep_store.save(make_episode(f"e_{i}", minutes_ago=i))
            ev_store.save(make_evidence(f"m_{i}", source_id=f"o_{i}", minutes_ago=i))

        view_a = assembler.build("alice", "s1")
        view_b = assembler.build("alice", "s1")
        assert view_a.recent_observation_ids == view_b.recent_observation_ids
        assert view_a.recent_episode_ids == view_b.recent_episode_ids
        assert view_a.retrieved_evidence_ids == view_b.retrieved_evidence_ids
        assert view_a.signals == view_b.signals
        assert view_a.counts == view_b.counts
        assert view_a.freshness_flags == view_b.freshness_flags


# ── consumer neutrality ──────────────────────────────────────────────


class TestConsumerNeutrality:
    def test_view_does_not_format_for_specific_consumer(self, assembler, stores):
        obs_store, _, _ = stores
        obs_store.save(make_obs("o1"))
        view = assembler.build("alice", "s1")
        # No prompt strings, no rendering, no agent routing fields
        assert not hasattr(view, "prompt")
        assert not hasattr(view, "system_message")
        assert not hasattr(view, "tool_calls")
        assert not hasattr(view, "ui_payload")
