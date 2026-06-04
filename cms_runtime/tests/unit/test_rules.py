"""Unit tests for evidence filing rules.

Covers:
  - observation-level rules fire correctly above thresholds
  - dead zones prevent neutral utterances from firing
  - mutual exclusion holds (cert ↔ hedge, self ↔ other)
  - episode-level rules fire on correct closure/length patterns
  - support_score is bounded in [0, 1] and monotonic with trigger strength
  - summaries are non-interpretive (no identity claims)
"""

from datetime import datetime, timedelta, timezone

import pytest

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l3.rules import (
    DEFAULT_EPISODE_RULES,
    DEFAULT_OBSERVATION_RULES,
    EPISODE_RUPTURE_MAX_LENGTH,
    EPISODE_SUSTAINED_MIN_LENGTH,
    EPISTEMIC_CERTAINTY_HIGH,
    EPISTEMIC_HEDGING_LOW,
    INTENT_OTHER_HIGH,
    INTENT_SELF_LOW,
    PRAGMATIC_RATIO_HIGH,
    rule_ep_dynamics_rupture,
    rule_ep_dynamics_sustained_regime,
    rule_ep_pragmatic_sustained_density,
    rule_obs_epistemic_certainty,
    rule_obs_epistemic_hedging,
    rule_obs_pragmatic_high_ratio,
    rule_obs_social_other_reference,
    rule_obs_social_self_reference,
)


# ── helpers ──────────────────────────────────────────────────────────


def make_obs(
    obs_id: str = "obs_001",
    cms_real=None,
    cms_imag=None,
) -> L1Observation:
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


def make_episode(
    length: int = 5,
    closure_reason: str = "window_full",
    trajectory_signature: dict | None = None,
    episode_id: str = "ep_001",
) -> L2Episode:
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return L2Episode(
        episode_id=episode_id,
        user_id="alice",
        session_id="s1",
        created_at=base,
        start_at=base,
        end_at=base + timedelta(seconds=length * 10),
        obs_ids=[f"obs_{i}" for i in range(length)],
        trajectory_signature=trajectory_signature or {},
        closure_reason=closure_reason,
    )


# ── pragmatic rule ───────────────────────────────────────────────────


class TestPragmaticHighRatioRule:
    def test_fires_when_ratio_clearly_high(self):
        # Re(z1) = 0.2, Im(z1) = 0.8 → ratio = 4.0 >> 1.5
        obs = make_obs(cms_real=[0.2, 0.5, 0.5], cms_imag=[0.8, 0.3, 0.3])
        payload = rule_obs_pragmatic_high_ratio(obs)

        assert payload is not None
        assert payload.scope == "pragmatic"
        assert payload.subscope == "high_pragmatic_ratio"
        assert payload.rule_id == "obs.pragmatic.high_ratio"
        assert 0.0 < payload.support_score <= 1.0
        assert "observation_level" in payload.tags
        assert "pragmatic_ratio" in payload.feature_snapshot

    def test_does_not_fire_below_threshold(self):
        # Re=0.8, Im=0.3 → ratio = 0.375, well below 1.5
        obs = make_obs(cms_real=[0.8, 0.5, 0.5], cms_imag=[0.3, 0.3, 0.3])
        assert rule_obs_pragmatic_high_ratio(obs) is None

    def test_support_score_monotonic_in_ratio(self):
        """Higher ratio → higher support."""
        low = make_obs(cms_real=[0.3, 0.5, 0.5], cms_imag=[0.6, 0.3, 0.3])   # 2.0
        high = make_obs(cms_real=[0.2, 0.5, 0.5], cms_imag=[0.9, 0.3, 0.3])  # 4.5

        p_low = rule_obs_pragmatic_high_ratio(low)
        p_high = rule_obs_pragmatic_high_ratio(high)
        assert p_low is not None and p_high is not None
        assert p_high.support_score >= p_low.support_score

    def test_does_not_fire_on_empty_cms(self):
        # edge case: degenerate observation
        obs = L1Observation(
            obs_id="x", user_id="alice", session_id="s1", turn_id="t",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            raw_text="", language=None,
            cms_real=[], cms_imag=[], temporal_phase=0.0,
        )
        assert rule_obs_pragmatic_high_ratio(obs) is None


# ── epistemic rules — mutual exclusion via dead zone ─────────────────


class TestEpistemicRules:
    def test_certainty_fires_above_threshold(self):
        obs = make_obs(cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.3])
        assert rule_obs_epistemic_certainty(obs) is not None
        # Hedging must NOT fire on same observation
        assert rule_obs_epistemic_hedging(obs) is None

    def test_hedging_fires_below_threshold(self):
        obs = make_obs(cms_real=[0.5, 0.2, 0.5], cms_imag=[0.3, 0.5, 0.3])
        assert rule_obs_epistemic_hedging(obs) is not None
        # Certainty must NOT fire on same observation
        assert rule_obs_epistemic_certainty(obs) is None

    def test_dead_zone_neither_fires(self):
        """Values in 0.35 < Re(z2) < 0.75 produce no evidence."""
        for val in [0.4, 0.5, 0.6, 0.7]:
            obs = make_obs(cms_real=[0.5, val, 0.5], cms_imag=[0.3, 0.5, 0.3])
            assert rule_obs_epistemic_certainty(obs) is None, (
                f"certainty fired at {val}"
            )
            assert rule_obs_epistemic_hedging(obs) is None, (
                f"hedging fired at {val}"
            )

    def test_certainty_and_hedging_mutually_exclusive_at_every_value(self):
        """For any z2 value, at most one of certainty/hedging fires."""
        for val in [0.0, 0.1, 0.2, 0.34, 0.35, 0.36, 0.5, 0.74, 0.75, 0.76, 0.9, 1.0]:
            obs = make_obs(cms_real=[0.5, val, 0.5], cms_imag=[0.3, 0.5, 0.3])
            cert = rule_obs_epistemic_certainty(obs)
            hedge = rule_obs_epistemic_hedging(obs)
            if cert is not None and hedge is not None:
                pytest.fail(f"both fired at z2={val}")

    def test_support_score_bounded(self):
        for val in [0.0, 0.2, 0.35, 0.5, 0.75, 0.9, 1.0]:
            obs = make_obs(cms_real=[0.5, val, 0.5], cms_imag=[0.3, 0.5, 0.3])
            for rule in (rule_obs_epistemic_certainty, rule_obs_epistemic_hedging):
                p = rule(obs)
                if p is not None:
                    assert 0.0 <= p.support_score <= 1.0


# ── social rules — mutual exclusion via dead zone ────────────────────


class TestSocialRules:
    def test_other_reference_fires_at_high(self):
        obs = make_obs(cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, 0.9])
        assert rule_obs_social_other_reference(obs) is not None
        assert rule_obs_social_self_reference(obs) is None

    def test_self_reference_fires_at_low(self):
        obs = make_obs(cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, 0.1])
        assert rule_obs_social_self_reference(obs) is not None
        assert rule_obs_social_other_reference(obs) is None

    def test_dead_zone_neither_fires(self):
        for val in [0.4, 0.5, 0.6, 0.7]:
            obs = make_obs(cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, val])
            assert rule_obs_social_self_reference(obs) is None
            assert rule_obs_social_other_reference(obs) is None

    def test_mutual_exclusion_across_spectrum(self):
        for val in [0.0, 0.2, 0.35, 0.5, 0.75, 0.9, 1.0]:
            obs = make_obs(cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, val])
            s = rule_obs_social_self_reference(obs)
            o = rule_obs_social_other_reference(obs)
            if s is not None and o is not None:
                pytest.fail(f"both fired at intent={val}")


# ── episode rules ────────────────────────────────────────────────────


class TestRuptureRule:
    def test_fires_on_short_surprise_closure(self):
        ep = make_episode(length=3, closure_reason="surprise_spike(score=2.1)")
        payload = rule_ep_dynamics_rupture(ep)
        assert payload is not None
        assert payload.scope == "dynamics"
        assert payload.subscope == "rupture"
        assert "episode_level" in payload.tags
        assert "surprise_closure" in payload.tags

    def test_does_not_fire_on_long_episodes(self):
        ep = make_episode(length=10, closure_reason="surprise_spike")
        assert rule_ep_dynamics_rupture(ep) is None

    def test_does_not_fire_on_non_surprise_closure(self):
        ep = make_episode(length=3, closure_reason="window_full")
        assert rule_ep_dynamics_rupture(ep) is None

    def test_shorter_episodes_yield_higher_support(self):
        short = make_episode(length=1, closure_reason="surprise_spike")
        longer = make_episode(length=4, closure_reason="surprise_spike")
        p_short = rule_ep_dynamics_rupture(short)
        p_longer = rule_ep_dynamics_rupture(longer)
        assert p_short is not None and p_longer is not None
        assert p_short.support_score > p_longer.support_score


class TestSustainedRegimeRule:
    def test_fires_on_long_natural_closure(self):
        ep = make_episode(length=15, closure_reason="window_full")
        payload = rule_ep_dynamics_sustained_regime(ep)
        assert payload is not None
        assert payload.scope == "dynamics"
        assert payload.subscope == "sustained_regime"

    def test_does_not_fire_on_short_episodes(self):
        ep = make_episode(length=5, closure_reason="window_full")
        assert rule_ep_dynamics_sustained_regime(ep) is None

    def test_does_not_fire_on_surprise_closure(self):
        ep = make_episode(length=15, closure_reason="surprise_spike")
        assert rule_ep_dynamics_sustained_regime(ep) is None

    def test_longer_episodes_yield_higher_support(self):
        short = make_episode(length=10, closure_reason="window_full")
        longer = make_episode(length=30, closure_reason="window_full")
        p_short = rule_ep_dynamics_sustained_regime(short)
        p_longer = rule_ep_dynamics_sustained_regime(longer)
        assert p_short is not None and p_longer is not None
        assert p_longer.support_score >= p_short.support_score


class TestSustainedPragmaticDensityRule:
    def test_fires_when_mean_ratio_high(self):
        ep = make_episode(
            length=10,
            trajectory_signature={"mean_pragmatic_ratio": 2.0},
        )
        payload = rule_ep_pragmatic_sustained_density(ep)
        assert payload is not None
        assert payload.scope == "pragmatic"
        assert payload.subscope == "sustained_pragmatic_density"

    def test_does_not_fire_when_ratio_low(self):
        ep = make_episode(
            length=10,
            trajectory_signature={"mean_pragmatic_ratio": 0.5},
        )
        assert rule_ep_pragmatic_sustained_density(ep) is None

    def test_does_not_fire_when_signature_missing(self):
        """No signature = no signal. This is correct behavior."""
        ep = make_episode(length=10, trajectory_signature={})
        assert rule_ep_pragmatic_sustained_density(ep) is None


# ── non-interpretive summaries ───────────────────────────────────────


class TestSummariesAreNonInterpretive:
    """Summaries MUST NOT make identity claims about the user."""

    FORBIDDEN_PATTERNS = [
        "user is ",          # "user is confident"
        "user prefers ",     # "user prefers directness"
        "user likes ",
        "user dislikes ",
        "user tends to be ",
        "user has a ",
    ]

    def _check_summary(self, summary: str):
        lower = summary.lower()
        for forbidden in self.FORBIDDEN_PATTERNS:
            assert forbidden not in lower, (
                f"Summary makes an identity claim: {summary!r}"
            )

    def test_all_observation_rules_produce_non_interpretive_summaries(self):
        # Construct an observation that triggers each rule in turn
        triggering_obs = [
            # pragmatic high ratio
            make_obs(cms_real=[0.2, 0.5, 0.5], cms_imag=[0.8, 0.5, 0.3]),
            # certainty
            make_obs(cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.3]),
            # hedging
            make_obs(cms_real=[0.5, 0.2, 0.5], cms_imag=[0.3, 0.5, 0.3]),
            # self_reference
            make_obs(cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, 0.1]),
            # other_reference
            make_obs(cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, 0.9]),
        ]

        for obs in triggering_obs:
            for rule in DEFAULT_OBSERVATION_RULES:
                payload = rule(obs)
                if payload is not None:
                    self._check_summary(payload.summary)

    def test_all_episode_rules_produce_non_interpretive_summaries(self):
        test_episodes = [
            make_episode(length=3, closure_reason="surprise_spike(score=2.1)"),
            make_episode(length=15, closure_reason="window_full"),
            make_episode(length=10, trajectory_signature={"mean_pragmatic_ratio": 2.0}),
        ]
        for ep in test_episodes:
            for rule in DEFAULT_EPISODE_RULES:
                payload = rule(ep)
                if payload is not None:
                    self._check_summary(payload.summary)
