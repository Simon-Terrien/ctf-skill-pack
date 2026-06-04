"""Unit tests for L2Episode dataclass invariants."""

from datetime import datetime, timedelta, timezone

import pytest

from cms.l2.episode import L2Episode


def _valid_kwargs(**overrides) -> dict:
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    base = {
        "episode_id": "ep_001",
        "user_id": "alice",
        "session_id": "s1",
        "created_at": base_time,
        "start_at": base_time,
        "end_at": base_time + timedelta(seconds=30),
        "obs_ids": ["obs_0", "obs_1", "obs_2"],
        "trajectory_signature": {"mean_pragmatic": 0.3},
        "surprise_score": 1.2,
        "drift_score": 0.5,
        "confidence_score": 0.8,
        "closure_reason": "window_full",
    }
    base.update(overrides)
    return base


class TestConstruction:
    def test_minimal_construction(self):
        ep = L2Episode(**_valid_kwargs())
        assert ep.episode_id == "ep_001"
        assert ep.length == 3

    def test_default_collections_independent(self):
        a = L2Episode(**_valid_kwargs(episode_id="a"))
        b = L2Episode(**_valid_kwargs(episode_id="b"))
        a.metadata["x"] = 1
        assert b.metadata == {}

    def test_default_scores_zero(self):
        kw = _valid_kwargs()
        del kw["surprise_score"]
        del kw["drift_score"]
        del kw["confidence_score"]
        ep = L2Episode(**kw)
        assert ep.surprise_score == 0.0
        assert ep.drift_score == 0.0
        assert ep.confidence_score == 0.0


class TestInvariants:
    def test_empty_episode_id_rejected(self):
        with pytest.raises(ValueError, match="episode_id"):
            L2Episode(**_valid_kwargs(episode_id=""))

    def test_empty_user_id_rejected(self):
        with pytest.raises(ValueError, match="user_id"):
            L2Episode(**_valid_kwargs(user_id=""))

    def test_empty_session_id_rejected(self):
        with pytest.raises(ValueError, match="session_id"):
            L2Episode(**_valid_kwargs(session_id=""))

    def test_empty_obs_ids_rejected(self):
        with pytest.raises(ValueError, match="at least one observation"):
            L2Episode(**_valid_kwargs(obs_ids=[]))

    def test_inverted_time_range_rejected(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="start_at"):
            L2Episode(**_valid_kwargs(
                start_at=base + timedelta(hours=1),
                end_at=base,
            ))


class TestConvenience:
    def test_length_matches_obs_ids(self):
        ep = L2Episode(**_valid_kwargs(obs_ids=["a", "b", "c", "d"]))
        assert ep.length == 4

    def test_duration_seconds(self):
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ep = L2Episode(**_valid_kwargs(
            start_at=base,
            end_at=base + timedelta(seconds=42.5),
        ))
        assert ep.duration_seconds == 42.5

    def test_zero_duration_episode(self):
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ep = L2Episode(**_valid_kwargs(start_at=base, end_at=base))
        assert ep.duration_seconds == 0.0
