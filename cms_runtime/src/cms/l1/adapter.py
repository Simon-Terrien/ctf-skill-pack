"""
Legacy feature extractor adapter.

Wraps the existing LinguisticFeatureExtractor from the research line
(cms_research/cms_core/models.py) and adapts its output to the
L1Observation contract.

Per the ADR, this is a *deliberate* adapter pattern:
  - The research extractor stays unchanged.
  - The runtime depends on a thin protocol (FeatureExtractor) so
    alternative extractors can be swapped in later without touching
    the ObservationService.

Equivalence guarantee
---------------------
For the same input text, the adapter produces an L1Observation whose
cms_real/cms_imag/features values are identical (within float tolerance)
to what the research extractor produces. This is enforced by
tests/integration/test_observation_equivalence.py.
"""

from __future__ import annotations

from typing import Protocol


class FeatureExtractorProtocol(Protocol):
    """Minimal contract a feature extractor must satisfy.

    The runtime only needs `extract_sentence_features`. Anything else
    (sentence splitting, full-text trajectory) stays in the research line.
    """

    def extract_sentence_features(self, sentence: str) -> dict[str, float]: ...


# CMS dimension layout — must stay stable across versions.
# This is the contract between L1 features and CMS coordinates.
#
#   z1 = semantic_density   + i * pragmatic_load        (semantic-pragmatic)
#   z2 = epistemic_certainty + i * temporal_orientation (epistemic-temporal)
#   z3 = topic_concreteness  + i * intent_direction     (topic-intent)
#
# Order matters: index 0 = z1, 1 = z2, 2 = z3.
CMS_DIMENSION_LAYOUT: tuple[tuple[str, str], ...] = (
    ("semantic_density",     "pragmatic_load"),
    ("epistemic_certainty",  "temporal_orientation"),
    ("topic_concreteness",   "intent_direction"),
)


class LegacyExtractorAdapter:
    """Adapt a research-line LinguisticFeatureExtractor for runtime use.

    Usage::

        from cms_research.cms_core.models import LinguisticFeatureExtractor
        from cms.l1.adapter import LegacyExtractorAdapter

        legacy = LinguisticFeatureExtractor()
        adapter = LegacyExtractorAdapter(legacy)
        cms_real, cms_imag, features = adapter.encode("The server is running.")
    """

    def __init__(self, extractor: FeatureExtractorProtocol):
        self._extractor = extractor

    def encode(
        self, sentence: str
    ) -> tuple[list[float], list[float], dict[str, float]]:
        """Extract features and project them onto CMS coordinates.

        Returns
        -------
        cms_real  : real components, ordered per CMS_DIMENSION_LAYOUT
        cms_imag  : imaginary components, ordered per CMS_DIMENSION_LAYOUT
        features  : full feature dict (includes extras beyond CMS coords)
        """
        features = self._extractor.extract_sentence_features(sentence)

        cms_real: list[float] = []
        cms_imag: list[float] = []
        for re_key, im_key in CMS_DIMENSION_LAYOUT:
            cms_real.append(float(features.get(re_key, 0.0)))
            cms_imag.append(float(features.get(im_key, 0.0)))

        return cms_real, cms_imag, features

    @staticmethod
    def compute_temporal_phase(turn_index: int, period: float = 32.0) -> float:
        """Map a turn index to a phase in [0, 2π).

        This is a deliberately simple temporal phase encoding — kept
        stable across the L1 slice. Future versions may use richer
        temporal models (real-time gap, conversational rhythm, etc.).
        """
        import math

        return (2.0 * math.pi * turn_index / period) % (2.0 * math.pi)
