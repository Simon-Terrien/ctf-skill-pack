"""
Bibi v1 feature extractor — deterministic, transparent, no model dependencies.

This is NOT the research-line LinguisticFeatureExtractor. It's a v1
heuristic extractor that produces values in the same shape (six floats
in [0, 1]) so the runtime's LegacyExtractorAdapter can consume it.

The rules are deliberately simple and inspectable:
  - epistemic_certainty: ratio of certainty markers vs hedging markers
  - intent_direction: ratio of self-reference vs other-reference pronouns
  - pragmatic_load: ratio of imperatives/questions vs declaratives
  - semantic_density: roughly content-word ratio vs function words
  - temporal_orientation: presence of temporal markers
  - topic_concreteness: punctuation/specificity heuristic

When Bibi v2 ships voice + research extractor integration, this gets
swapped out by replacing the extractor in BibiApp's wiring. The runtime
contract (six floats in [0,1]) does not change.

Design rule: keep the rules simple enough that "why did Bibi infer X"
can be answered by looking at the text.
"""

from __future__ import annotations

import re


# Marker sets — bilingual EN/FR to match Pylo's actual usage
CERTAINTY_MARKERS = frozenset({
    # English
    "definitely", "certainly", "absolutely", "obviously", "clearly",
    "always", "never", "must", "will", "is", "are",
    # French
    "certainement", "absolument", "evidemment", "clairement",
    "toujours", "jamais", "doit", "doivent",
})

HEDGING_MARKERS = frozenset({
    # English
    "maybe", "perhaps", "possibly", "might", "could", "may",
    "sometimes", "occasionally", "i think", "i guess", "kind of",
    "sort of", "probably", "seems",
    # French
    "peut-etre", "peut", "pourrait", "parfois", "je pense",
    "je crois", "probablement", "semble",
})

SELF_PRONOUNS = frozenset({
    "i", "me", "my", "mine", "myself",
    "je", "moi", "mon", "ma", "mes", "me",
})

OTHER_PRONOUNS = frozenset({
    "you", "your", "yours", "yourself",
    "we", "us", "our", "ours",
    "they", "them", "their", "theirs",
    "tu", "te", "ton", "ta", "tes",
    "vous", "votre", "vos",
    "ils", "elles", "leur", "leurs",
})

TEMPORAL_MARKERS = frozenset({
    "today", "yesterday", "tomorrow", "now", "soon", "later",
    "before", "after", "next", "last", "morning", "evening",
    "aujourd'hui", "hier", "demain", "maintenant", "bientot",
    "matin", "soir", "avant", "apres",
})


_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokenization — keeps apostrophes, drops punctuation."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _ratio(numer: int, denom: int) -> float:
    """Safe ratio, clamped to [0, 1]."""
    if denom <= 0:
        return 0.0
    return max(0.0, min(1.0, numer / denom))


class BibiTextExtractor:
    """Deterministic feature extractor for Bibi v1.

    Implements the same `extract_sentence_features(sentence) -> dict`
    interface that the runtime's LegacyExtractorAdapter expects. The
    runtime doesn't care that this isn't the research extractor — it
    just consumes the six features.
    """

    def extract_sentence_features(self, sentence: str) -> dict[str, float]:
        tokens = _tokenize(sentence)
        n_tokens = len(tokens)

        if n_tokens == 0:
            # Empty input → neutral features
            return {
                "semantic_density": 0.5,
                "pragmatic_load": 0.3,
                "epistemic_certainty": 0.5,
                "temporal_orientation": 0.5,
                "topic_concreteness": 0.5,
                "intent_direction": 0.5,
            }

        # Multi-word phrase detection (lowercase substring)
        sentence_lower = sentence.lower()
        certainty_phrase_hits = sum(
            1 for m in CERTAINTY_MARKERS if " " in m and m in sentence_lower
        )
        hedging_phrase_hits = sum(
            1 for m in HEDGING_MARKERS if " " in m and m in sentence_lower
        )

        # Single-token markers
        certainty_word_hits = sum(
            1 for t in tokens if t in CERTAINTY_MARKERS
        )
        hedging_word_hits = sum(
            1 for t in tokens if t in HEDGING_MARKERS
        )

        certainty_total = certainty_phrase_hits + certainty_word_hits
        hedging_total = hedging_phrase_hits + hedging_word_hits

        # Epistemic certainty: where the marker balance falls
        # 0.5 = neutral, >0.5 = certainty-leaning, <0.5 = hedging-leaning
        if certainty_total + hedging_total == 0:
            epistemic = 0.5
        else:
            epistemic = certainty_total / (certainty_total + hedging_total)

        # Intent direction: self vs other pronoun balance
        self_hits = sum(1 for t in tokens if t in SELF_PRONOUNS)
        other_hits = sum(1 for t in tokens if t in OTHER_PRONOUNS)
        if self_hits + other_hits == 0:
            direction = 0.5
        else:
            # Match runtime convention: high = other-directed, low = self-directed
            direction = other_hits / (self_hits + other_hits)

        # Pragmatic load: imperatives, questions, exclamations
        # Heuristic: question marks, exclamation marks, leading verbs
        question_marks = sentence.count("?")
        exclam_marks = sentence.count("!")
        # Sentences are usually 1 here (we get one at a time via the runtime)
        pragmatic = _ratio(
            question_marks + exclam_marks,
            max(1, sentence.count(".") + question_marks + exclam_marks),
        )
        # Bias toward 0.3 when no strong signal — runtime's neutral default
        if question_marks + exclam_marks == 0:
            pragmatic = 0.3

        # Semantic density: rough content-word ratio
        # Function-word heuristic — short tokens tend to be function words
        # in both English and French
        content_tokens = sum(1 for t in tokens if len(t) >= 4)
        density = _ratio(content_tokens, n_tokens)

        # Temporal orientation: presence of temporal markers
        temporal_hits = sum(1 for t in tokens if t in TEMPORAL_MARKERS)
        # Match runtime convention: 0.5 neutral
        if temporal_hits == 0:
            temporal = 0.5
        else:
            temporal = min(1.0, 0.5 + 0.1 * temporal_hits)

        # Topic concreteness: punctuation specificity heuristic
        # Numbers, capitalized non-initial words, named entities-ish
        n_digits = sum(1 for c in sentence if c.isdigit())
        n_caps_internal = sum(
            1 for i, c in enumerate(sentence)
            if c.isupper() and i > 0 and sentence[i - 1] != "."
        )
        concreteness = min(1.0, 0.5 + 0.05 * (n_digits + n_caps_internal))

        return {
            "semantic_density": density,
            "pragmatic_load": pragmatic,
            "epistemic_certainty": epistemic,
            "temporal_orientation": temporal,
            "topic_concreteness": concreteness,
            "intent_direction": direction,
        }
