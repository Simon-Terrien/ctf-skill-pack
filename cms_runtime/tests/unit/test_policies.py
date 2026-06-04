"""Unit tests for episode closure policies."""

from datetime import datetime, timezone

import pytest

from cms.l1.observation import L1Observation
from cms.l2.policies import (
    CompositeClosurePolicy,
    EuclideanSurpriseScorer,
    SurpriseClosurePolicy,
    WindowedClosurePolicy,
)


def make_obs(obs_id: str = "obs_0", cms_real=None, cms_imag=None) -> L1Observation:
    return L1Observation(
        obs_id=obs_id,
        user_id="alice",
        session_id="s1",
        turn_id=f"t_{obs_id}",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_text="text",
        language="en",
        cms_real=cms_real or [0.5, 0.5, 0.5],
        cms_imag=cms_imag or [0.3, 0.3, 0.3],
        temporal_phase=0.0,
    )


class TestWindowedPolicy:
    def test_does_not_close_below_window(self):
        policy = WindowedClosurePolicy(max_size=5)
        open_obs = [make_obs(f"o{i}") for i in range(3)]
        close, reason = policy.should_close(make_obs("next"), open_obs)
        assert close is False
        assert reason == ""

    def test_closes_when_window_would_exceed(self):
        policy = WindowedClosurePolicy(max_size=5)
        open_obs = [make_obs(f"o{i}") for i in range(4)]
        # +1 for next_obs = 5, hits threshold
        close, reason = policy.should_close(make_obs("next"), open_obs)
        assert close is True
        assert reason == "window_full"

    def test_invalid_max_size_rejected(self):
        with pytest.raises(ValueError, match="max_size"):
            WindowedClosurePolicy(max_size=0)

    def test_closes_at_min_size_one(self):
        policy = WindowedClosurePolicy(max_size=1)
        # Empty open: next would make size 1, hits threshold
        close, reason = policy.should_close(make_obs("next"), [])
        assert close is True


class TestSurprisePolicy:
    def test_does_not_close_with_insufficient_history(self):
        scorer = EuclideanSurpriseScorer()
        policy = SurpriseClosurePolicy(scorer, threshold=0.001, min_history=5)
        # Only 2 in history, min_history is 5
        open_obs = [make_obs(f"o{i}") for i in range(2)]
        close, reason = policy.should_close(make_obs("next"), open_obs)
        assert close is False

    def test_closes_on_high_surprise(self):
        scorer = EuclideanSurpriseScorer()
        policy = SurpriseClosurePolicy(scorer, threshold=2.0, min_history=3)

        # Build a stable baseline of identical observations
        open_obs = [
            make_obs(f"o{i}", cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3])
            for i in range(8)
        ]
        # Inject tiny variance so std isn't zero
        open_obs[3] = make_obs("o3", cms_real=[0.51, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3])
        open_obs[5] = make_obs("o5", cms_real=[0.5, 0.49, 0.5], cms_imag=[0.3, 0.3, 0.3])

        # Strongly different next observation
        next_obs = make_obs("next", cms_real=[0.95, 0.95, 0.95], cms_imag=[0.95, 0.95, 0.95])

        close, reason = policy.should_close(next_obs, open_obs)
        assert close is True
        assert "surprise_spike" in reason

    def test_does_not_close_on_consistent_input(self):
        scorer = EuclideanSurpriseScorer()
        policy = SurpriseClosurePolicy(scorer, threshold=10.0, min_history=3)

        # Baseline with real variance — simulating normal conversation noise
        import random
        rng = random.Random(42)
        open_obs = [
            make_obs(
                f"o{i}",
                cms_real=[0.5 + rng.uniform(-0.05, 0.05) for _ in range(3)],
                cms_imag=[0.3 + rng.uniform(-0.05, 0.05) for _ in range(3)],
            )
            for i in range(8)
        ]
        # Next observation within baseline variance
        next_obs = make_obs(
            "next",
            cms_real=[0.5 + rng.uniform(-0.05, 0.05) for _ in range(3)],
            cms_imag=[0.3 + rng.uniform(-0.05, 0.05) for _ in range(3)],
        )

        close, _ = policy.should_close(next_obs, open_obs)
        assert close is False

    def test_invalid_threshold_rejected(self):
        with pytest.raises(ValueError, match="threshold"):
            SurpriseClosurePolicy(EuclideanSurpriseScorer(), threshold=0)

    def test_invalid_min_history_rejected(self):
        with pytest.raises(ValueError, match="min_history"):
            SurpriseClosurePolicy(EuclideanSurpriseScorer(), min_history=0)


class TestCompositePolicy:
    def test_closes_if_any_policy_says_so(self):
        scorer = EuclideanSurpriseScorer()
        composite = CompositeClosurePolicy([
            WindowedClosurePolicy(max_size=100),  # won't trigger
            SurpriseClosurePolicy(scorer, threshold=2.0, min_history=3),
        ])

        open_obs = [make_obs(f"o{i}") for i in range(8)]
        # Force variance
        open_obs[3] = make_obs("o3", cms_real=[0.51, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3])
        next_obs = make_obs("next", cms_real=[0.95]*3, cms_imag=[0.95]*3)

        close, reason = composite.should_close(next_obs, open_obs)
        assert close is True
        assert "surprise_spike" in reason

    def test_does_not_close_if_no_policy_says_so(self):
        scorer = EuclideanSurpriseScorer()
        composite = CompositeClosurePolicy([
            WindowedClosurePolicy(max_size=100),
            SurpriseClosurePolicy(scorer, threshold=10.0, min_history=3),
        ])
        open_obs = [make_obs(f"o{i}") for i in range(5)]
        close, _ = composite.should_close(make_obs("next"), open_obs)
        assert close is False

    def test_combines_multiple_close_reasons(self):
        # Both windowed and surprise close
        scorer = EuclideanSurpriseScorer()
        composite = CompositeClosurePolicy([
            WindowedClosurePolicy(max_size=5),  # will trigger
            SurpriseClosurePolicy(scorer, threshold=0.001, min_history=3),  # will trigger
        ])
        open_obs = [make_obs(f"o{i}") for i in range(4)]
        # Make some variance so surprise is meaningful
        open_obs[2] = make_obs("o2", cms_real=[0.51]*3, cms_imag=[0.3]*3)
        next_obs = make_obs("next", cms_real=[0.6]*3, cms_imag=[0.4]*3)

        close, reason = composite.should_close(next_obs, open_obs)
        assert close is True
        assert "window_full" in reason
        assert "surprise_spike" in reason

    def test_empty_policy_list_rejected(self):
        with pytest.raises(ValueError, match="at least one policy"):
            CompositeClosurePolicy([])


class TestEuclideanScorer:
    def test_zero_history_returns_zero(self):
        scorer = EuclideanSurpriseScorer()
        assert scorer.score(make_obs(), []) == 0.0

    def test_identical_input_low_surprise(self):
        scorer = EuclideanSurpriseScorer()
        recent = [make_obs(f"o{i}") for i in range(10)]
        # Add tiny variance so std isn't zero
        recent[3] = make_obs("o3", cms_real=[0.51, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3])
        next_obs = make_obs("next")
        score = scorer.score(next_obs, recent)
        # Score should be small (near baseline)
        assert abs(score) < 5.0

    def test_outlier_input_high_surprise(self):
        scorer = EuclideanSurpriseScorer()
        recent = [make_obs(f"o{i}") for i in range(10)]
        recent[3] = make_obs("o3", cms_real=[0.51, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3])
        next_obs = make_obs("next", cms_real=[0.99]*3, cms_imag=[0.99]*3)
        score = scorer.score(next_obs, recent)
        # Outlier should produce large positive z-score
        assert score > 1.0

    def test_score_clipped_to_finite(self):
        """Score must not return inf/nan even with degenerate input."""
        scorer = EuclideanSurpriseScorer()
        # All identical: std → 0, would be inf without clipping
        recent = [make_obs(f"o{i}") for i in range(10)]
        next_obs = make_obs("next", cms_real=[0.99]*3, cms_imag=[0.99]*3)
        score = scorer.score(next_obs, recent)
        assert -10.0 <= score <= 10.0
