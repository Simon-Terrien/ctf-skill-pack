"""Unit tests for EvidenceService.

Key behaviors validated:
  - observation-level rules fire and persist evidence with full provenance
  - episode-level rules fire and persist evidence
  - idempotency: re-filing same source produces no new records
  - scope policing: rules producing non-canonical scopes raise
  - rule packs are pluggable
  - mutual exclusion is preserved end-to-end (service does not override rules)
"""

from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from cms.l1.observation import L1Observation
from cms.l2.episode import L2Episode
from cms.l3.evidence import CANONICAL_SCOPES
from cms.l3.rules import (
    EvidencePayload,
    RULE_OBS_EPISTEMIC_CERTAINTY,
    RULE_OBS_EPISTEMIC_HEDGING,
    RULE_OBS_PRAGMATIC_HIGH,
    RULE_OBS_SOCIAL_OTHER,
    RULE_OBS_SOCIAL_SELF,
    RULE_EP_DYNAMICS_RUPTURE,
)
from cms.l3.service import EvidenceService
from cms.storage.evidence_store import EvidenceStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def store():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    yield EvidenceStore(backend)
    backend.close()


@pytest.fixture
def fixed_clock():
    return lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sequential_ids():
    counter = count(0)
    return lambda: f"mem_{next(counter):04d}"


@pytest.fixture
def service(store, fixed_clock, sequential_ids):
    return EvidenceService(
        store=store,
        clock=fixed_clock,
        id_factory=sequential_ids,
    )


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
    episode_id: str = "ep_001",
    length: int = 3,
    closure_reason: str = "surprise_spike(score=2.5)",
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
        trajectory_signature={},
        closure_reason=closure_reason,
    )


# ── observation-level filing ─────────────────────────────────────────


class TestFileFromObservation:
    def test_high_certainty_produces_evidence(self, service, store):
        # Neutral intent (0.5) so only the certainty rule fires
        obs = make_obs(cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.5])
        records = service.file_from_observation(obs)

        assert len(records) == 1
        r = records[0]
        assert r.rule_id == RULE_OBS_EPISTEMIC_CERTAINTY
        assert r.scope == "epistemic"
        assert r.source_kind == "observation"
        assert r.source_id == "obs_001"
        assert r.user_id == "alice"
        # Persisted
        assert store.get(r.memory_id) is not None

    def test_neutral_observation_produces_no_evidence(self, service):
        """Dead-zone observation produces nothing."""
        obs = make_obs(cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, 0.5])
        records = service.file_from_observation(obs)
        assert records == []

    def test_high_pragmatic_ratio_produces_evidence(self, service):
        obs = make_obs(cms_real=[0.2, 0.5, 0.5], cms_imag=[0.8, 0.5, 0.5])
        records = service.file_from_observation(obs)
        rule_ids = {r.rule_id for r in records}
        assert RULE_OBS_PRAGMATIC_HIGH in rule_ids

    def test_mutual_exclusion_certainty_vs_hedging(self, service):
        # High certainty — only cert fires, never hedge
        obs_high = make_obs(cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.5])
        rule_ids = {r.rule_id for r in service.file_from_observation(obs_high)}
        assert RULE_OBS_EPISTEMIC_CERTAINTY in rule_ids
        assert RULE_OBS_EPISTEMIC_HEDGING not in rule_ids

    def test_mutual_exclusion_self_vs_other(self, service):
        # High other-reference
        obs = make_obs(cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, 0.9])
        rule_ids = {r.rule_id for r in service.file_from_observation(obs)}
        assert RULE_OBS_SOCIAL_OTHER in rule_ids
        assert RULE_OBS_SOCIAL_SELF not in rule_ids

    def test_multiple_rules_can_fire_on_same_observation(self, service):
        # High certainty + strong other-orientation
        obs = make_obs(cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.85])
        records = service.file_from_observation(obs)
        rule_ids = {r.rule_id for r in records}
        assert RULE_OBS_EPISTEMIC_CERTAINTY in rule_ids
        assert RULE_OBS_SOCIAL_OTHER in rule_ids

    def test_provenance_fully_populated(self, service):
        obs = make_obs(obs_id="obs_xyz", cms_real=[0.5, 0.9, 0.5],
                       cms_imag=[0.3, 0.5, 0.3])
        records = service.file_from_observation(obs)
        assert len(records) >= 1
        r = records[0]
        assert r.source_kind == "observation"
        assert r.source_id == "obs_xyz"
        assert r.rule_id.startswith("obs.")
        assert r.feature_snapshot  # non-empty for rules that consult features


# ── episode-level filing ─────────────────────────────────────────────


class TestFileFromEpisode:
    def test_rupture_rule_fires_for_short_surprise_close(self, service, store):
        ep = make_episode(length=3, closure_reason="surprise_spike(score=2.5)")
        records = service.file_from_episode(ep)
        rule_ids = {r.rule_id for r in records}
        assert RULE_EP_DYNAMICS_RUPTURE in rule_ids

        r = records[0]
        assert r.source_kind == "episode"
        assert r.source_id == "ep_001"
        assert store.get(r.memory_id) is not None

    def test_no_evidence_for_natural_short_closure(self, service):
        ep = make_episode(length=3, closure_reason="window_full")
        records = service.file_from_episode(ep)
        assert records == []


# ── idempotency ──────────────────────────────────────────────────────


class TestIdempotency:
    def test_refile_same_observation_produces_nothing(self, service, store):
        obs = make_obs(cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.5])

        first = service.file_from_observation(obs)
        assert len(first) >= 1

        # Re-file the same observation — no new records
        second = service.file_from_observation(obs)
        assert second == []

        # Total count unchanged
        assert store.count_for_user("alice") == len(first)

    def test_refile_same_episode_produces_nothing(self, service, store):
        ep = make_episode(length=3, closure_reason="surprise_spike")

        first = service.file_from_episode(ep)
        assert len(first) >= 1

        second = service.file_from_episode(ep)
        assert second == []

    def test_different_observations_file_independently(self, service):
        obs_a = make_obs(obs_id="obs_a", cms_real=[0.5, 0.9, 0.5],
                         cms_imag=[0.3, 0.5, 0.5])
        obs_b = make_obs(obs_id="obs_b", cms_real=[0.5, 0.9, 0.5],
                         cms_imag=[0.3, 0.5, 0.5])

        records_a = service.file_from_observation(obs_a)
        records_b = service.file_from_observation(obs_b)

        # Both produce evidence — different source_ids, different idempotency keys
        assert len(records_a) >= 1
        assert len(records_b) >= 1
        assert records_a[0].source_id != records_b[0].source_id

    def test_different_users_file_independently(self, service):
        obs_alice = L1Observation(
            obs_id="obs_001", user_id="alice", session_id="s1",
            turn_id="t0",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            raw_text="text", language="en",
            cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.5],
            temporal_phase=0.0,
        )
        obs_bob = L1Observation(
            obs_id="obs_001",  # same source_id!
            user_id="bob", session_id="s1", turn_id="t0",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            raw_text="text", language="en",
            cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.5],
            temporal_phase=0.0,
        )

        records_alice = service.file_from_observation(obs_alice)
        records_bob = service.file_from_observation(obs_bob)

        # Both users get their own evidence
        assert len(records_alice) >= 1
        assert len(records_bob) >= 1


# ── scope policing ───────────────────────────────────────────────────


class TestScopePolicing:
    def test_non_canonical_scope_raises(self, store):
        """A rule that produces a scope outside CANONICAL_SCOPES must be rejected."""

        def rogue_rule(obs):
            return EvidencePayload(
                rule_id="rogue.rule",
                scope="identity",   # NOT canonical
                subscope="trait",
                summary="rogue rule output",
                support_score=0.5,
            )

        service = EvidenceService(
            store=store,
            observation_rules=[rogue_rule],
            episode_rules=[],
        )
        obs = make_obs()
        with pytest.raises(ValueError, match="non-canonical scope"):
            service.file_from_observation(obs)

    def test_custom_canonical_scopes_enforced(self, store):
        """Callers can tighten the canonical set further."""
        restrictive_service = EvidenceService(
            store=store,
            canonical_scopes=frozenset({"epistemic"}),  # only this
        )
        # Pragmatic rule would be rejected
        obs = make_obs(cms_real=[0.2, 0.5, 0.5], cms_imag=[0.8, 0.5, 0.5])
        with pytest.raises(ValueError, match="non-canonical scope"):
            restrictive_service.file_from_observation(obs)

    def test_all_default_rules_produce_canonical_scopes(self, service):
        """Sanity: the default rule pack respects CANONICAL_SCOPES."""
        # Trigger each rule with a tailored observation
        triggers = [
            make_obs(obs_id="o1", cms_real=[0.2, 0.5, 0.5], cms_imag=[0.8, 0.5, 0.5]),
            make_obs(obs_id="o2", cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.5]),
            make_obs(obs_id="o3", cms_real=[0.5, 0.2, 0.5], cms_imag=[0.3, 0.5, 0.5]),
            make_obs(obs_id="o4", cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, 0.1]),
            make_obs(obs_id="o5", cms_real=[0.5, 0.5, 0.5], cms_imag=[0.3, 0.5, 0.9]),
        ]
        for obs in triggers:
            records = service.file_from_observation(obs)
            for r in records:
                assert r.scope in CANONICAL_SCOPES


# ── pluggable rule packs ─────────────────────────────────────────────


class TestPluggableRules:
    def test_empty_rule_pack_files_nothing(self, store):
        service = EvidenceService(
            store=store,
            observation_rules=[],
            episode_rules=[],
        )
        obs = make_obs(cms_real=[0.5, 0.9, 0.5], cms_imag=[0.3, 0.5, 0.5])
        assert service.file_from_observation(obs) == []

    def test_custom_rule_fires(self, store):
        def custom_rule(obs):
            if obs.cms_real[1] > 0.7:
                return EvidencePayload(
                    rule_id="custom.test_rule",
                    scope="epistemic",
                    subscope="test",
                    summary="custom rule fired",
                    support_score=0.9,
                )
            return None

        service = EvidenceService(
            store=store,
            observation_rules=[custom_rule],
            episode_rules=[],
        )
        obs = make_obs(cms_real=[0.5, 0.8, 0.5], cms_imag=[0.3, 0.5, 0.5])
        records = service.file_from_observation(obs)
        assert len(records) == 1
        assert records[0].rule_id == "custom.test_rule"
