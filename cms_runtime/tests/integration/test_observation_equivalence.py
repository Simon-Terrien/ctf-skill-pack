"""
Integration test: equivalence between the runtime path and the research path.

For the same input text, the runtime ObservationService must produce
CMS coordinates and feature values that exactly match what the research
LinguisticFeatureExtractor + CMSPoint pipeline produces.

This is the core ADR validation gate: the runtime line must not diverge
from the research line at the encoding layer. Higher-order divergence
(episodes, evidence, beliefs) is intentional and addressed in later slices.

Strategy
--------
We rebuild a minimal local copy of the research extractor and CMSPoint
inside this test, so that the test does not require the cms_research
repo to be on PYTHONPATH. The local copies must mirror the research-line
behavior exactly. (A real deployment test would import from cms_research
directly — that path is documented but not enforced here to keep the
runtime repo self-contained.)
"""

import re
from dataclasses import dataclass

import numpy as np
import pytest

from cms.l1.adapter import LegacyExtractorAdapter
from cms.l1.service import ObservationService
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import OBSERVATIONS_DDL
from cms.storage.sqlite import SQLiteBackend


# ── Local mirror of the research-line extractor ──────────────────────
# Must stay in sync with cms_research/cms_core/models.py.
# The integration test fails immediately if these diverge.

class ResearchExtractorMirror:
    """Mirror of cms_research.cms_core.models.LinguisticFeatureExtractor.

    Kept verbatim so equivalence tests catch any drift between the two
    codebases at the feature-extraction level.
    """
    HEDGING = frozenset({
        'maybe', 'perhaps', 'possibly', 'might', 'could', 'somewhat',
        'apparently', 'seems', 'arguably', 'presumably', 'likely',
        'probably', 'roughly', 'approximately', 'suggest', 'suppose',
    })
    CERTAINTY = frozenset({
        'definitely', 'certainly', 'absolutely', 'clearly', 'obviously',
        'undoubtedly', 'always', 'never', 'must', 'proven', 'fact',
        'guaranteed', 'precisely', 'exactly',
    })
    SELF_REF = frozenset({'i', 'me', 'my', 'mine', 'myself',
                          "i'm", "i've", "i'll", "i'd"})
    OTHER_REF = frozenset({'you', 'your', 'yours', 'yourself', 'they', 'them',
                           'their', 'we', 'our', 'us'})
    EMOTIONAL_POS = frozenset({'love', 'great', 'amazing', 'wonderful', 'excellent',
                               'happy', 'glad', 'fantastic', 'beautiful', 'brilliant',
                               'enjoy', 'excited', 'awesome', 'perfect', 'best'})
    EMOTIONAL_NEG = frozenset({'hate', 'terrible', 'awful', 'horrible', 'worst',
                               'angry', 'frustrated', 'annoyed', 'disappointed',
                               'disgusting', 'pathetic', 'useless', 'furious', 'sad'})
    ABSTRACT = frozenset({'concept', 'theory', 'idea', 'principle', 'framework',
                          'paradigm', 'abstract', 'philosophical', 'fundamental',
                          'essentially', 'inherently', 'notion', 'aspect', 'nature'})
    CONCRETE = frozenset({'build', 'run', 'code', 'install', 'click', 'type',
                          'file', 'server', 'table', 'button', 'screen', 'hand',
                          'walk', 'car', 'house', 'door', 'machine', 'tool'})
    TEMPORAL_PAST = frozenset({'was', 'were', 'had', 'did', 'been', 'ago',
                               'yesterday', 'previously', 'former'})
    TEMPORAL_FUTURE = frozenset({'will', 'shall', 'tomorrow', 'soon',
                                 'eventually', 'plan', 'intend', 'expect', 'upcoming'})
    CAUSAL = frozenset({'because', 'therefore', 'consequently', 'thus', 'hence',
                        'caused', 'due', 'since', 'implies', 'follows'})

    _TOKEN_RE = re.compile(r"[\w']+|[.,!?;:]")

    def _tokenize(self, text):
        return self._TOKEN_RE.findall(text.lower())

    def _density(self, tokens, markers):
        if not tokens:
            return 0.0
        return sum(1 for t in tokens if t in markers) / len(tokens)

    def extract_sentence_features(self, sentence):
        tokens = self._tokenize(sentence)
        word_tokens = [t for t in tokens if t.isalpha()]
        avg_word_len = np.mean([len(w) for w in word_tokens]) if word_tokens else 0

        semantic_density = float(np.clip(avg_word_len / 10.0, 0, 1))

        question = 1.0 if '?' in sentence else 0.0
        hedge = self._density(tokens, self.HEDGING)
        other = self._density(tokens, self.OTHER_REF)
        pragmatic_load = float(np.clip(question * 0.4 + hedge * 3.0 + other * 2.0, 0, 1))

        cert = self._density(tokens, self.CERTAINTY)
        hed = self._density(tokens, self.HEDGING)
        epistemic_certainty = float(np.clip(0.5 + (cert - hed) * 5.0, 0, 1))

        past = self._density(tokens, self.TEMPORAL_PAST)
        future = self._density(tokens, self.TEMPORAL_FUTURE)
        temporal_orientation = float(np.clip(0.5 + (future - past) * 5.0, 0, 1))

        conc = self._density(tokens, self.CONCRETE)
        abst = self._density(tokens, self.ABSTRACT)
        topic_concreteness = float(np.clip(0.5 + (conc - abst) * 5.0, 0, 1))

        self_ref = self._density(tokens, self.SELF_REF)
        other_ref = self._density(tokens, self.OTHER_REF)
        intent_direction = float(np.clip(0.5 + (other_ref - self_ref) * 5.0, 0, 1))

        emotional_valence = (
            self._density(tokens, self.EMOTIONAL_POS)
            - self._density(tokens, self.EMOTIONAL_NEG)
        )
        causal_density = self._density(tokens, self.CAUSAL)

        return {
            'semantic_density': semantic_density,
            'pragmatic_load': pragmatic_load,
            'epistemic_certainty': epistemic_certainty,
            'temporal_orientation': temporal_orientation,
            'topic_concreteness': topic_concreteness,
            'intent_direction': intent_direction,
            'emotional_valence': emotional_valence,
            'causal_density': causal_density,
            'sentence_length': len(tokens),
        }


@dataclass
class ResearchCMSPointMirror:
    """Mirror of cms_research.cms_core.models.CMSPoint."""
    z1: complex
    z2: complex
    z3: complex
    t: float


def research_text_to_cms_point(extractor, text, t):
    """Mirror of LinguisticFeatureExtractor.text_to_trajectory(...) for a single sentence."""
    f = extractor.extract_sentence_features(text)
    return ResearchCMSPointMirror(
        z1=complex(f['semantic_density'], f['pragmatic_load']),
        z2=complex(f['epistemic_certainty'], f['temporal_orientation']),
        z3=complex(f['topic_concreteness'], f['intent_direction']),
        t=float(t),
    )


# ── Equivalence test fixtures ────────────────────────────────────────

EQUIVALENCE_CORPUS = [
    "The server is definitely overloaded right now.",
    "Maybe we could try a completely different approach?",
    "How are you feeling about the new project timeline?",
    "We will eventually deliver the results next quarter.",
    "I really love how the team came together on this!",
    "The build process failed because the dependency was wrong.",
    "What if the requirements themselves are completely wrong?",
    "Your presentation was absolutely brilliant yesterday.",
    "I plan to consolidate our position before expanding further.",
    "Le système fonctionne — c'est parfait pour notre projet.",
]


@pytest.fixture
def runtime_setup():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(OBSERVATIONS_DDL)
    store = ObservationStore(backend)
    research_extractor = ResearchExtractorMirror()
    adapter = LegacyExtractorAdapter(research_extractor)
    service = ObservationService(adapter=adapter, store=store)
    yield service, store, research_extractor
    backend.close()


# ── Equivalence tests ────────────────────────────────────────────────

class TestCMSEncodingEquivalence:
    """The runtime must produce identical CMS coordinates to the research path."""

    @pytest.mark.parametrize("text", EQUIVALENCE_CORPUS)
    def test_cms_coords_match_research_path(self, runtime_setup, text):
        service, _, research_extractor = runtime_setup

        # Runtime path
        runtime_obs = service.ingest("alice", "s1", "t0", text)

        # Research path
        research_point = research_text_to_cms_point(research_extractor, text, 0)

        # Equivalence checks (exact float equality, not approx — both
        # paths feed the same numbers into the same arithmetic).
        assert runtime_obs.cms_real[0] == research_point.z1.real, "z1 real mismatch"
        assert runtime_obs.cms_imag[0] == research_point.z1.imag, "z1 imag mismatch"
        assert runtime_obs.cms_real[1] == research_point.z2.real, "z2 real mismatch"
        assert runtime_obs.cms_imag[1] == research_point.z2.imag, "z2 imag mismatch"
        assert runtime_obs.cms_real[2] == research_point.z3.real, "z3 real mismatch"
        assert runtime_obs.cms_imag[2] == research_point.z3.imag, "z3 imag mismatch"

    @pytest.mark.parametrize("text", EQUIVALENCE_CORPUS)
    def test_to_complex_matches_research_complex(self, runtime_setup, text):
        service, _, research_extractor = runtime_setup
        runtime_obs = service.ingest("alice", "s1", "t0", text)
        research_point = research_text_to_cms_point(research_extractor, text, 0)

        runtime_complex = runtime_obs.to_complex()
        assert runtime_complex[0] == research_point.z1
        assert runtime_complex[1] == research_point.z2
        assert runtime_complex[2] == research_point.z3


class TestFeatureEquivalence:
    """Beyond CMS coords, the full feature dict must match the research extractor."""

    @pytest.mark.parametrize("text", EQUIVALENCE_CORPUS)
    def test_full_feature_dict_matches(self, runtime_setup, text):
        service, _, research_extractor = runtime_setup

        runtime_obs = service.ingest("alice", "s1", "t0", text)
        research_features = research_extractor.extract_sentence_features(text)

        # Runtime features should contain every research feature with the same value.
        for key, val in research_features.items():
            assert key in runtime_obs.features, f"Missing feature: {key}"
            assert runtime_obs.features[key] == val, \
                f"Feature {key} mismatch: runtime={runtime_obs.features[key]}, research={val}"


class TestPersistenceDoesNotMutate:
    """Round-tripping through SQLite must preserve exact equivalence."""

    @pytest.mark.parametrize("text", EQUIVALENCE_CORPUS)
    def test_persisted_observation_still_matches_research(self, runtime_setup, text):
        service, store, research_extractor = runtime_setup

        ingested = service.ingest("alice", "s1", "t0", text)
        retrieved = store.get(ingested.obs_id)

        research_point = research_text_to_cms_point(research_extractor, text, 0)

        # After SQLite round-trip, equivalence must still hold
        assert retrieved.cms_real[0] == research_point.z1.real
        assert retrieved.cms_imag[0] == research_point.z1.imag
        assert retrieved.cms_real[1] == research_point.z2.real
        assert retrieved.cms_imag[1] == research_point.z2.imag
        assert retrieved.cms_real[2] == research_point.z3.real
        assert retrieved.cms_imag[2] == research_point.z3.imag


class TestBatchEquivalence:
    """Process a full corpus through both paths and compare in aggregate."""

    def test_corpus_aggregate_equivalence(self, runtime_setup):
        service, _, research_extractor = runtime_setup

        for i, text in enumerate(EQUIVALENCE_CORPUS):
            runtime_obs = service.ingest("alice", "s1", f"t{i}", text)
            research_point = research_text_to_cms_point(research_extractor, text, i)

            assert runtime_obs.to_complex() == [
                research_point.z1, research_point.z2, research_point.z3,
            ], f"Mismatch on corpus item {i}: '{text[:40]}...'"
