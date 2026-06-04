"""Unit tests for LegacyExtractorAdapter."""

import math

import pytest

from cms.l1.adapter import CMS_DIMENSION_LAYOUT, LegacyExtractorAdapter


class FakeExtractor:
    """Stub matching FeatureExtractorProtocol."""

    def __init__(self, features: dict[str, float]):
        self._features = features
        self.calls: list[str] = []

    def extract_sentence_features(self, sentence: str) -> dict[str, float]:
        self.calls.append(sentence)
        return dict(self._features)


class TestEncode:
    def test_extracts_all_six_cms_coordinates(self):
        features = {
            "semantic_density": 0.9,
            "pragmatic_load": 0.1,
            "epistemic_certainty": 0.8,
            "temporal_orientation": 0.5,
            "topic_concreteness": 0.7,
            "intent_direction": 0.4,
        }
        adapter = LegacyExtractorAdapter(FakeExtractor(features))
        cms_real, cms_imag, returned = adapter.encode("any text")

        assert cms_real == [0.9, 0.8, 0.7]
        assert cms_imag == [0.1, 0.5, 0.4]
        assert returned == features

    def test_missing_features_default_to_zero(self):
        # Only semantic_density present — others should default to 0.0
        adapter = LegacyExtractorAdapter(FakeExtractor({"semantic_density": 0.42}))
        cms_real, cms_imag, _ = adapter.encode("text")

        assert cms_real == [0.42, 0.0, 0.0]
        assert cms_imag == [0.0, 0.0, 0.0]

    def test_dimension_layout_is_stable(self):
        """The CMS_DIMENSION_LAYOUT is a contract — verify its shape."""
        assert len(CMS_DIMENSION_LAYOUT) == 3
        assert CMS_DIMENSION_LAYOUT[0] == ("semantic_density", "pragmatic_load")
        assert CMS_DIMENSION_LAYOUT[1] == ("epistemic_certainty", "temporal_orientation")
        assert CMS_DIMENSION_LAYOUT[2] == ("topic_concreteness", "intent_direction")

    def test_extra_features_passed_through(self):
        """Features not in the CMS layout still appear in the returned dict."""
        features = {
            "semantic_density": 0.5,
            "pragmatic_load": 0.5,
            "epistemic_certainty": 0.5,
            "temporal_orientation": 0.5,
            "topic_concreteness": 0.5,
            "intent_direction": 0.5,
            "emotional_valence": 0.3,  # extra
            "causal_density": 0.1,     # extra
        }
        adapter = LegacyExtractorAdapter(FakeExtractor(features))
        _, _, returned = adapter.encode("text")
        assert returned["emotional_valence"] == 0.3
        assert returned["causal_density"] == 0.1


class TestTemporalPhase:
    def test_zero_at_start(self):
        assert LegacyExtractorAdapter.compute_temporal_phase(0) == 0.0

    def test_phase_in_valid_range(self):
        for i in range(100):
            phase = LegacyExtractorAdapter.compute_temporal_phase(i)
            assert 0.0 <= phase < 2 * math.pi

    def test_period_wraps(self):
        """At i = period, phase should wrap back to ~0."""
        phase = LegacyExtractorAdapter.compute_temporal_phase(32, period=32.0)
        assert phase == pytest.approx(0.0, abs=1e-9)

    def test_half_period(self):
        phase = LegacyExtractorAdapter.compute_temporal_phase(16, period=32.0)
        assert phase == pytest.approx(math.pi, abs=1e-9)
