"""
Evidence filing rules — Block 3.

A rule is a callable with a stable contract:

    def rule(source) -> EvidencePayload | None

A payload is either returned (fire) or None (no fire). Rules are:
    - deterministic (same input → same output)
    - local (consult only the source object, not global state)
    - rule-owned (the summary text lives with the rule, not the service)
    - explainable (simple threshold logic)

Per the locked contract:
    - Scope stays in {"pragmatic", "epistemic", "social", "dynamics"}
    - Mutual exclusion is enforced at the rule layer:
          certainty and hedging cannot both fire on one observation
          self_reference and other_reference cannot both fire on one observation
    - Dead zones separate triggers from neutral territory.

Block 3 rule pack
-----------------
Observation-level (5 rules):
    obs.pragmatic.high_ratio
    obs.epistemic.certainty
    obs.epistemic.hedging
    obs.social.self_reference
    obs.social.other_reference

Episode-level (3 rules):
    ep.dynamics.rupture
    ep.dynamics.sustained_regime
    ep.pragmatic.sustained_density

Rule ids are stable strings — they feed directly into the idempotency key.
Never rename a rule id without a migration plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode


# ── Threshold constants (tunable, exposed for testing/config) ────────
#
# Dead zones: the gap between positive and negative triggers. Values
# between the two thresholds produce no evidence — this is deliberate,
# it keeps the evidence signal sparse and interpretable.

# CMS z1: semantic_density (Re) + pragmatic_load (Im)
# pragmatic_ratio = |Im(z1)| / |Re(z1)|
PRAGMATIC_RATIO_HIGH = 1.5  # above → high_ratio fires

# CMS z2: epistemic_certainty (Re) + temporal_orientation (Im)
EPISTEMIC_CERTAINTY_HIGH = 0.75  # above → certainty
EPISTEMIC_HEDGING_LOW    = 0.35  # below → hedging
# Dead zone: 0.35 < Re(z2) < 0.75

# CMS z3: topic_concreteness (Re) + intent_direction (Im)
# intent_direction: 0.0 = fully self-oriented, 1.0 = fully other-oriented
INTENT_OTHER_HIGH = 0.75  # above → other_reference
INTENT_SELF_LOW   = 0.35  # below → self_reference
# Dead zone: 0.35 < Im(z3) < 0.75


# Episode-level thresholds
EPISODE_RUPTURE_MAX_LENGTH = 5       # short episode
EPISODE_SUSTAINED_MIN_LENGTH = 10    # long episode
EPISODE_SUSTAINED_PRAGMATIC_THRESHOLD = 1.0  # mean pragmatic ratio


# ── Rule id constants ────────────────────────────────────────────────
#
# Stable identifiers. Never rename without a migration plan. These are
# part of the evidence idempotency key.

# Observation-level
RULE_OBS_PRAGMATIC_HIGH      = "obs.pragmatic.high_ratio"
RULE_OBS_EPISTEMIC_CERTAINTY = "obs.epistemic.certainty"
RULE_OBS_EPISTEMIC_HEDGING   = "obs.epistemic.hedging"
RULE_OBS_SOCIAL_SELF         = "obs.social.self_reference"
RULE_OBS_SOCIAL_OTHER        = "obs.social.other_reference"

# Episode-level
RULE_EP_DYNAMICS_RUPTURE           = "ep.dynamics.rupture"
RULE_EP_DYNAMICS_SUSTAINED         = "ep.dynamics.sustained_regime"
RULE_EP_PRAGMATIC_SUSTAINED        = "ep.pragmatic.sustained_density"


# ── Payload type ─────────────────────────────────────────────────────


@dataclass(slots=True)
class EvidencePayload:
    """Output of a rule firing — everything the service needs to build
    a MemoryEvidence record except the top-level identity fields
    (memory_id, user_id, created_at, source_kind, source_id).
    """
    rule_id: str
    scope: str
    subscope: str
    summary: str
    support_score: float
    tags: list[str] = field(default_factory=list)
    feature_snapshot: dict[str, float] = field(default_factory=dict)


# Rule signatures (structural types — actual rules are plain functions)
ObservationRule = Callable[[L1Observation], EvidencePayload | None]
EpisodeRule = Callable[[L2Episode], EvidencePayload | None]


# ── Helpers ──────────────────────────────────────────────────────────


def _clip01(x: float) -> float:
    """Clip to [0, 1] — support and relevance scores must be bounded."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _pragmatic_ratio(obs: L1Observation) -> float:
    """|Im(z1)| / |Re(z1)| with safe denominator handling."""
    if obs.cms_dim == 0:
        return 0.0
    re = abs(obs.cms_real[0])
    im = abs(obs.cms_imag[0])
    if re < 1e-10:
        # Effectively infinite — cap for scoring stability
        return 100.0 if im > 0 else 0.0
    return im / re


# ── Observation-level rules ──────────────────────────────────────────


def rule_obs_pragmatic_high_ratio(obs: L1Observation) -> EvidencePayload | None:
    """Fire when |Im(z1)| / |Re(z1)| is clearly high.

    High pragmatic ratio = utterance's pragmatic content dominates its
    semantic content. Requests, hedges, questions, indirect speech.
    """
    ratio = _pragmatic_ratio(obs)
    if ratio < PRAGMATIC_RATIO_HIGH:
        return None

    # support_score: how far above threshold we are, bounded to [0, 1]
    # Normalize: ratio 1.5 → 0.0, ratio 3.0+ → 1.0 (capped)
    excess = ratio - PRAGMATIC_RATIO_HIGH
    support = _clip01(excess / 1.5)

    return EvidencePayload(
        rule_id=RULE_OBS_PRAGMATIC_HIGH,
        scope="pragmatic",
        subscope="high_pragmatic_ratio",
        summary="utterance exhibited high pragmatic load relative to semantic density",
        support_score=support,
        tags=["observation_level"],
        feature_snapshot={
            "cms_real_z1": obs.cms_real[0] if obs.cms_dim > 0 else 0.0,
            "cms_imag_z1": obs.cms_imag[0] if obs.cms_dim > 0 else 0.0,
            "pragmatic_ratio": ratio if ratio < 100.0 else 100.0,
        },
    )


def rule_obs_epistemic_certainty(obs: L1Observation) -> EvidencePayload | None:
    """Fire when Re(z2) is clearly above the certainty threshold.

    Mutual exclusion with hedging is automatic: Re(z2) cannot be both
    above 0.75 and below 0.35.
    """
    if obs.cms_dim < 2:
        return None
    certainty = obs.cms_real[1]
    if certainty < EPISTEMIC_CERTAINTY_HIGH:
        return None

    # support_score: distance above threshold, normalized
    # 0.75 → 0.0, 1.0 → 1.0
    excess = certainty - EPISTEMIC_CERTAINTY_HIGH
    support = _clip01(excess / (1.0 - EPISTEMIC_CERTAINTY_HIGH))

    return EvidencePayload(
        rule_id=RULE_OBS_EPISTEMIC_CERTAINTY,
        scope="epistemic",
        subscope="certainty",
        summary="utterance expressed high epistemic certainty",
        support_score=support,
        tags=["observation_level"],
        feature_snapshot={"epistemic_certainty": certainty},
    )


def rule_obs_epistemic_hedging(obs: L1Observation) -> EvidencePayload | None:
    """Fire when Re(z2) is clearly below the hedging threshold.

    Mutual exclusion with certainty is automatic (dead zone).
    """
    if obs.cms_dim < 2:
        return None
    certainty = obs.cms_real[1]
    if certainty > EPISTEMIC_HEDGING_LOW:
        return None

    # support_score: distance below threshold, normalized
    # 0.35 → 0.0, 0.0 → 1.0
    below = EPISTEMIC_HEDGING_LOW - certainty
    support = _clip01(below / EPISTEMIC_HEDGING_LOW)

    return EvidencePayload(
        rule_id=RULE_OBS_EPISTEMIC_HEDGING,
        scope="epistemic",
        subscope="hedging",
        summary="utterance expressed epistemic hedging",
        support_score=support,
        tags=["observation_level"],
        feature_snapshot={"epistemic_certainty": certainty},
    )


def rule_obs_social_self_reference(obs: L1Observation) -> EvidencePayload | None:
    """Fire when Im(z3) is clearly below the self-reference threshold.

    Im(z3) = intent_direction: 0.0 fully self-oriented, 1.0 fully other-oriented.
    Mutual exclusion with other_reference is automatic (dead zone).
    """
    if obs.cms_dim < 3:
        return None
    intent = obs.cms_imag[2]
    if intent > INTENT_SELF_LOW:
        return None

    below = INTENT_SELF_LOW - intent
    support = _clip01(below / INTENT_SELF_LOW)

    return EvidencePayload(
        rule_id=RULE_OBS_SOCIAL_SELF,
        scope="social",
        subscope="self_reference",
        summary="utterance was strongly self-referential",
        support_score=support,
        tags=["observation_level"],
        feature_snapshot={"intent_direction": intent},
    )


def rule_obs_social_other_reference(obs: L1Observation) -> EvidencePayload | None:
    """Fire when Im(z3) is clearly above the other-reference threshold.

    Mutual exclusion with self_reference is automatic (dead zone).
    """
    if obs.cms_dim < 3:
        return None
    intent = obs.cms_imag[2]
    if intent < INTENT_OTHER_HIGH:
        return None

    excess = intent - INTENT_OTHER_HIGH
    support = _clip01(excess / (1.0 - INTENT_OTHER_HIGH))

    return EvidencePayload(
        rule_id=RULE_OBS_SOCIAL_OTHER,
        scope="social",
        subscope="other_reference",
        summary="utterance was strongly other-oriented",
        support_score=support,
        tags=["observation_level"],
        feature_snapshot={"intent_direction": intent},
    )


# ── Episode-level rules ──────────────────────────────────────────────


def rule_ep_dynamics_rupture(ep: L2Episode) -> EvidencePayload | None:
    """Fire for short episodes closed by surprise.

    Short + surprise-triggered = the regime broke early. A sign the user's
    trajectory changed dynamics before settling.
    """
    if ep.length > EPISODE_RUPTURE_MAX_LENGTH:
        return None
    if "surprise" not in ep.closure_reason.lower():
        return None

    # support_score: shorter = stronger rupture, bounded by max length
    # length 1 → 1.0, length = max → 0.0
    length_factor = 1.0 - ((ep.length - 1) / max(EPISODE_RUPTURE_MAX_LENGTH - 1, 1))
    support = _clip01(length_factor)

    return EvidencePayload(
        rule_id=RULE_EP_DYNAMICS_RUPTURE,
        scope="dynamics",
        subscope="rupture",
        summary="episode ended in early rupture",
        support_score=support,
        tags=["episode_level", "surprise_closure"],
        feature_snapshot={
            "episode_length": float(ep.length),
            "duration_seconds": ep.duration_seconds,
        },
    )


def rule_ep_dynamics_sustained_regime(ep: L2Episode) -> EvidencePayload | None:
    """Fire for long episodes closed naturally (not by surprise)."""
    if ep.length < EPISODE_SUSTAINED_MIN_LENGTH:
        return None
    if "surprise" in ep.closure_reason.lower():
        return None

    # support_score: longer and less surprising = stronger sustain
    # length 10 → 0.0, length 30+ → 1.0
    length_factor = (ep.length - EPISODE_SUSTAINED_MIN_LENGTH) / 20.0
    support = _clip01(length_factor)

    return EvidencePayload(
        rule_id=RULE_EP_DYNAMICS_SUSTAINED,
        scope="dynamics",
        subscope="sustained_regime",
        summary="episode showed sustained stable regime",
        support_score=support,
        tags=["episode_level", "natural_closure"],
        feature_snapshot={
            "episode_length": float(ep.length),
            "duration_seconds": ep.duration_seconds,
        },
    )


def rule_ep_pragmatic_sustained_density(ep: L2Episode) -> EvidencePayload | None:
    """Fire when the episode's trajectory signature shows high mean pragmatic ratio.

    Requires the trajectory_signature to contain a 'mean_pragmatic_ratio' key.
    If the signature was not computed, the rule does not fire — this is correct
    behavior (no signature = no signal).
    """
    mean_ratio = ep.trajectory_signature.get("mean_pragmatic_ratio")
    if mean_ratio is None:
        return None
    if mean_ratio < EPISODE_SUSTAINED_PRAGMATIC_THRESHOLD:
        return None

    # support_score: excess over threshold, normalized
    excess = mean_ratio - EPISODE_SUSTAINED_PRAGMATIC_THRESHOLD
    support = _clip01(excess / EPISODE_SUSTAINED_PRAGMATIC_THRESHOLD)

    return EvidencePayload(
        rule_id=RULE_EP_PRAGMATIC_SUSTAINED,
        scope="pragmatic",
        subscope="sustained_pragmatic_density",
        summary="episode showed sustained high pragmatic density",
        support_score=support,
        tags=["episode_level"],
        feature_snapshot={"mean_pragmatic_ratio": mean_ratio},
    )


# ── Default rule packs ───────────────────────────────────────────────
# Exposed so callers can register custom subsets if needed.

DEFAULT_OBSERVATION_RULES: tuple[ObservationRule, ...] = (
    rule_obs_pragmatic_high_ratio,
    rule_obs_epistemic_certainty,
    rule_obs_epistemic_hedging,
    rule_obs_social_self_reference,
    rule_obs_social_other_reference,
)

DEFAULT_EPISODE_RULES: tuple[EpisodeRule, ...] = (
    rule_ep_dynamics_rupture,
    rule_ep_dynamics_sustained_regime,
    rule_ep_pragmatic_sustained_density,
)
