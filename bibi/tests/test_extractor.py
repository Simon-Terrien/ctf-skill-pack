"""Tests for BibiTextExtractor — deterministic feature extraction."""

import pytest

from bibi.extractor import BibiTextExtractor


@pytest.fixture
def extractor():
    return BibiTextExtractor()


# ── shape ────────────────────────────────────────────────────────────


class TestFeatureShape:
    def test_six_features_returned(self, extractor):
        features = extractor.extract_sentence_features("hello world")
        assert set(features) == {
            "semantic_density", "pragmatic_load", "epistemic_certainty",
            "temporal_orientation", "topic_concreteness", "intent_direction",
        }

    def test_all_features_in_unit_range(self, extractor):
        features = extractor.extract_sentence_features(
            "I am absolutely certain that this is the right approach."
        )
        for k, v in features.items():
            assert 0.0 <= v <= 1.0, f"{k} out of range: {v}"

    def test_empty_input_returns_neutral(self, extractor):
        features = extractor.extract_sentence_features("")
        assert features["epistemic_certainty"] == 0.5
        assert features["intent_direction"] == 0.5


# ── epistemic certainty ──────────────────────────────────────────────


class TestEpistemicCertainty:
    def test_certainty_markers_push_high(self, extractor):
        f = extractor.extract_sentence_features(
            "This is absolutely certain and definitely correct."
        )
        assert f["epistemic_certainty"] > 0.7

    def test_hedging_markers_push_low(self, extractor):
        f = extractor.extract_sentence_features(
            "Maybe this could possibly be the right approach, perhaps."
        )
        assert f["epistemic_certainty"] < 0.3

    def test_neutral_text_stays_neutral(self, extractor):
        f = extractor.extract_sentence_features(
            "The sun rose over the hills."
        )
        assert f["epistemic_certainty"] == 0.5

    def test_french_certainty_recognized(self, extractor):
        f = extractor.extract_sentence_features(
            "C'est absolument certain et certainement correct."
        )
        assert f["epistemic_certainty"] > 0.7

    def test_french_hedging_recognized(self, extractor):
        f = extractor.extract_sentence_features(
            "Peut-etre que je pense que c'est probablement vrai."
        )
        assert f["epistemic_certainty"] < 0.3


# ── intent direction ────────────────────────────────────────────────


class TestIntentDirection:
    def test_self_pronouns_push_low(self, extractor):
        f = extractor.extract_sentence_features("I think my approach is mine.")
        assert f["intent_direction"] < 0.3

    def test_other_pronouns_push_high(self, extractor):
        f = extractor.extract_sentence_features(
            "You should consider your approach and what they want."
        )
        assert f["intent_direction"] > 0.7

    def test_no_pronouns_stays_neutral(self, extractor):
        f = extractor.extract_sentence_features("The book is on the table.")
        assert f["intent_direction"] == 0.5


# ── pragmatic load ──────────────────────────────────────────────────


class TestPragmaticLoad:
    def test_questions_raise_pragmatic(self, extractor):
        f = extractor.extract_sentence_features("Could you help me?")
        assert f["pragmatic_load"] > 0.5

    def test_declaratives_stay_low(self, extractor):
        f = extractor.extract_sentence_features("The system works correctly.")
        assert f["pragmatic_load"] <= 0.3


# ── determinism ─────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_input_same_output(self, extractor):
        text = "I am working on the AI security course today."
        a = extractor.extract_sentence_features(text)
        b = extractor.extract_sentence_features(text)
        assert a == b
