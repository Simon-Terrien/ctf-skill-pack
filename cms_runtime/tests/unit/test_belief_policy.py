"""Unit tests for BeliefThresholds and the staleness helper."""

from datetime import datetime, timedelta, timezone

import pytest

from cms.l3.belief_policy import BeliefThresholds, is_belief_stale


# ── thresholds invariants ────────────────────────────────────────────


class TestBeliefThresholdsInvariants:
    def test_defaults_match_locked_contract(self):
        t = BeliefThresholds()
        assert t.tentative_min_support == 1
        assert t.active_min_support == 3
        assert t.active_window_days == 30
        assert t.active_min_confidence == 0.5
        assert t.stale_window_days == 14
        assert t.invalidation_burst_count == 3
        assert t.invalidation_burst_window_days == 7

    def test_min_supporting_strength_must_be_in_range(self):
        with pytest.raises(ValueError, match="min_supporting_strength"):
            BeliefThresholds(min_supporting_strength=-0.1)
        with pytest.raises(ValueError, match="min_supporting_strength"):
            BeliefThresholds(min_supporting_strength=1.5)

    def test_tentative_must_be_at_least_one(self):
        with pytest.raises(ValueError, match="tentative_min_support"):
            BeliefThresholds(tentative_min_support=0)

    def test_active_must_be_at_least_tentative(self):
        with pytest.raises(ValueError, match="active_min_support"):
            BeliefThresholds(tentative_min_support=5, active_min_support=3)

    def test_active_min_confidence_in_range(self):
        with pytest.raises(ValueError, match="active_min_confidence"):
            BeliefThresholds(active_min_confidence=1.5)

    def test_window_days_must_be_positive(self):
        with pytest.raises(ValueError, match="active_window_days"):
            BeliefThresholds(active_window_days=0)
        with pytest.raises(ValueError, match="stale_window_days"):
            BeliefThresholds(stale_window_days=0)

    def test_burst_count_must_be_positive(self):
        with pytest.raises(ValueError, match="invalidation_burst_count"):
            BeliefThresholds(invalidation_burst_count=0)


# ── timedelta convenience ────────────────────────────────────────────


class TestTimedeltaConvenience:
    def test_stale_window_returns_timedelta(self):
        t = BeliefThresholds(stale_window_days=14)
        assert t.stale_window == timedelta(days=14)

    def test_active_window_returns_timedelta(self):
        t = BeliefThresholds(active_window_days=30)
        assert t.active_window == timedelta(days=30)

    def test_invalidation_burst_window_returns_timedelta(self):
        t = BeliefThresholds(invalidation_burst_window_days=7)
        assert t.invalidation_burst_window == timedelta(days=7)


# ── staleness helper ─────────────────────────────────────────────────


class TestIsBeliefStale:
    def test_no_support_ever_is_stale(self):
        t = BeliefThresholds()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert is_belief_stale(None, now, t) is True

    def test_recent_support_is_fresh(self):
        t = BeliefThresholds(stale_window_days=14)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        last = now - timedelta(days=5)
        assert is_belief_stale(last, now, t) is False

    def test_old_support_is_stale(self):
        t = BeliefThresholds(stale_window_days=14)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        last = now - timedelta(days=30)
        assert is_belief_stale(last, now, t) is True

    def test_boundary_at_threshold_is_fresh(self):
        """Exactly at the window edge counts as fresh."""
        t = BeliefThresholds(stale_window_days=14)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        last = now - timedelta(days=14)
        assert is_belief_stale(last, now, t) is False

    def test_just_past_threshold_is_stale(self):
        t = BeliefThresholds(stale_window_days=14)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        last = now - timedelta(days=14, seconds=1)
        assert is_belief_stale(last, now, t) is True
