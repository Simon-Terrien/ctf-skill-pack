"""Unit tests for RetrievalPolicy and RuntimeStateView."""

from datetime import datetime, timezone

import pytest

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l3.evidence import MemoryEvidence
from cms.runtime.state import RetrievalPolicy, RuntimeStateView


def make_obs(obs_id: str = "obs_001") -> L1Observation:
    return L1Observation(
        obs_id=obs_id, user_id="alice", session_id="s1", turn_id="t",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_text="x", language="en",
        cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3],
        temporal_phase=0.0,
    )


def make_episode(episode_id: str = "ep_001") -> L2Episode:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return L2Episode(
        episode_id=episode_id, user_id="alice", session_id="s1",
        created_at=base, start_at=base, end_at=base,
        obs_ids=["obs_001"],
    )


def make_evidence(memory_id: str = "mem_001") -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=memory_id, user_id="alice",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source_kind="observation", source_id="obs_001",
        rule_id="obs.epistemic.certainty",
        scope="epistemic", subscope="certainty",
        summary="x", support_score=0.5, relevance_score=1.0,
    )


# ── RetrievalPolicy ──────────────────────────────────────────────────


class TestRetrievalPolicy:
    def test_defaults(self):
        p = RetrievalPolicy()
        assert p.observation_limit == 5
        assert p.episode_limit == 3
        assert p.evidence_limit == 5

    def test_custom_values(self):
        p = RetrievalPolicy(observation_limit=10, episode_limit=2, evidence_limit=20)
        assert p.observation_limit == 10
        assert p.episode_limit == 2
        assert p.evidence_limit == 20

    def test_negative_limits_rejected(self):
        with pytest.raises(ValueError, match="observation_limit"):
            RetrievalPolicy(observation_limit=-1)
        with pytest.raises(ValueError, match="episode_limit"):
            RetrievalPolicy(episode_limit=-1)
        with pytest.raises(ValueError, match="evidence_limit"):
            RetrievalPolicy(evidence_limit=-1)

    def test_zero_limits_allowed(self):
        # Zero is a valid choice (means "skip this layer")
        p = RetrievalPolicy(observation_limit=0, episode_limit=0, evidence_limit=0)
        assert p.observation_limit == 0


# ── RuntimeStateView ─────────────────────────────────────────────────


class TestRuntimeStateView:
    def test_minimal_construction(self):
        view = RuntimeStateView(
            user_id="alice", session_id="s1",
            current_observation=None,
            recent_observations=[], recent_episodes=[], retrieved_evidence=[],
        )
        assert view.user_id == "alice"
        assert view.signals == {}
        assert view.counts == {}
        assert view.freshness_flags == {}

    def test_id_convenience_properties(self):
        obs1, obs2 = make_obs("obs_001"), make_obs("obs_002")
        ep1, ep2 = make_episode("ep_001"), make_episode("ep_002")
        ev1, ev2 = make_evidence("mem_001"), make_evidence("mem_002")
        view = RuntimeStateView(
            user_id="alice", session_id="s1",
            current_observation=obs1,
            recent_observations=[obs1, obs2],
            recent_episodes=[ep1, ep2],
            retrieved_evidence=[ev1, ev2],
        )
        assert view.current_observation_id == "obs_001"
        assert view.recent_observation_ids == ["obs_001", "obs_002"]
        assert view.recent_episode_ids == ["ep_001", "ep_002"]
        assert view.retrieved_evidence_ids == ["mem_001", "mem_002"]

    def test_current_observation_id_is_none_when_no_current(self):
        view = RuntimeStateView(
            user_id="alice", session_id="s1",
            current_observation=None,
            recent_observations=[], recent_episodes=[], retrieved_evidence=[],
        )
        assert view.current_observation_id is None

    def test_signals_counts_freshness_separate(self):
        view = RuntimeStateView(
            user_id="alice", session_id="s1",
            current_observation=None,
            recent_observations=[], recent_episodes=[], retrieved_evidence=[],
            signals={"latest_age_seconds": 42.0},
            counts={"recent_observations": 5},
            freshness_flags={"has_recent_observations": True},
        )
        assert view.signals == {"latest_age_seconds": 42.0}
        assert view.counts == {"recent_observations": 5}
        assert view.freshness_flags == {"has_recent_observations": True}

    def test_default_collections_independent(self):
        a = RuntimeStateView(
            user_id="alice", session_id="s1",
            current_observation=None,
            recent_observations=[], recent_episodes=[], retrieved_evidence=[],
        )
        b = RuntimeStateView(
            user_id="bob", session_id="s1",
            current_observation=None,
            recent_observations=[], recent_episodes=[], retrieved_evidence=[],
        )
        a.signals["x"] = 1.0
        a.counts["y"] = 1
        a.freshness_flags["z"] = True
        assert b.signals == {}
        assert b.counts == {}
        assert b.freshness_flags == {}
