"""CMS Runtime L3 — memory evidence (L3A) and profile beliefs (L3B).

Block 3 delivered L3A (evidence). Block 5 adds L3B (beliefs).

Beliefs are evidence-backed interpretive records with explicit
lifecycle (tentative → active → stale / invalidated). They never
read directly from observations or episodes — only from evidence.
"""

# L3A — evidence
from cms.l3.evidence import CANONICAL_SCOPES, MemoryEvidence, SourceKind
from cms.l3.rules import (
    DEFAULT_EPISODE_RULES,
    DEFAULT_OBSERVATION_RULES,
    EpisodeRule,
    EvidencePayload,
    ObservationRule,
    RULE_EP_DYNAMICS_RUPTURE,
    RULE_EP_DYNAMICS_SUSTAINED,
    RULE_EP_PRAGMATIC_SUSTAINED,
    RULE_OBS_EPISTEMIC_CERTAINTY,
    RULE_OBS_EPISTEMIC_HEDGING,
    RULE_OBS_PRAGMATIC_HIGH,
    RULE_OBS_SOCIAL_OTHER,
    RULE_OBS_SOCIAL_SELF,
)
from cms.l3.service import EvidenceService

# L3B — beliefs
from cms.l3.belief import (
    DIMENSION_SPECS,
    DimensionSpec,
    ProfileBelief,
    BeliefStatus,
    VALID_BELIEF_STATUSES,
    dimension_for_scope,
)
from cms.l3.belief_policy import BeliefThresholds, is_belief_stale
from cms.l3.belief_service import BeliefService
from cms.l3.belief_events import (
    BeliefEvent,
    BeliefEventHandler,
    LoggingEventHandler,
    NullEventHandler,
)
from cms.l3.belief_explanation import BeliefExplanation

__all__ = [
    # L3A
    "MemoryEvidence",
    "SourceKind",
    "CANONICAL_SCOPES",
    "EvidencePayload",
    "EvidenceService",
    "ObservationRule",
    "EpisodeRule",
    "DEFAULT_OBSERVATION_RULES",
    "DEFAULT_EPISODE_RULES",
    "RULE_OBS_PRAGMATIC_HIGH",
    "RULE_OBS_EPISTEMIC_CERTAINTY",
    "RULE_OBS_EPISTEMIC_HEDGING",
    "RULE_OBS_SOCIAL_SELF",
    "RULE_OBS_SOCIAL_OTHER",
    "RULE_EP_DYNAMICS_RUPTURE",
    "RULE_EP_DYNAMICS_SUSTAINED",
    "RULE_EP_PRAGMATIC_SUSTAINED",
    # L3B
    "ProfileBelief",
    "BeliefStatus",
    "VALID_BELIEF_STATUSES",
    "DimensionSpec",
    "DIMENSION_SPECS",
    "dimension_for_scope",
    "BeliefThresholds",
    "is_belief_stale",
    "BeliefService",
    # Block 6 additions
    "BeliefEvent",
    "BeliefEventHandler",
    "NullEventHandler",
    "LoggingEventHandler",
    "BeliefExplanation",
]
