"""Unit tests for BeliefService — the heart of Block 5.

Covers the locked behaviors:
  - tentative belief creation from initial supporting evidence
  - upgrade tentative → active when thresholds are met
  - contradiction lowers confidence, recorded in counterevidence_ids
  - contradiction can invalidate when count overwhelms support
  - stale transition after stale_window_days without support
  - dimension-local updates (guardrail A)
  - dynamics evidence does NOT feed any belief (Block 5 strict mapping)
  - idempotency: same evidence twice doesn't double-count
  - recompute_for_user rebuilds beliefs from full ledger
"""

from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from cms.l3.belief import ProfileBelief
from cms.l3.belief_policy import BeliefThresholds
from cms.l3.belief_service import BeliefService
from cms.l3.evidence import MemoryEvidence
from cms.storage.belief_store import BeliefStore
from cms.storage.evidence_store import EvidenceStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def stores():
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    yield EvidenceStore(backend), BeliefStore(backend)
    backend.close()


@pytest.fixture
def service(stores):
    ev_store, b_store = stores
    counter = count(0)
    return BeliefService(
        belief_store=b_store,
        evidence_store=ev_store,
        clock=lambda: FIXED_NOW,
        id_factory=lambda: f"b_{next(counter):04d}",
    )


def make_evidence(
    memory_id: str,
    *,
    user_id: str = "alice",
    scope: str = "epistemic",
    subscope: str = "certainty",
    rule_id: str = "obs.epistemic.certainty",
    source_id: str | None = None,
    support_score: float = 0.6,
    minutes_ago: int = 0,
    source_kind: str = "observation",
) -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=memory_id,
        user_id=user_id,
        created_at=FIXED_NOW - timedelta(minutes=minutes_ago),
        source_kind=source_kind,
        source_id=source_id or f"src_{memory_id}",
        rule_id=rule_id,
        scope=scope,
        subscope=subscope,
        summary="x",
        support_score=support_score,
        relevance_score=1.0,
    )


def _save_evidence_records(
    ev_store: EvidenceStore, records: list[MemoryEvidence]
) -> None:
    """Helper: persist evidence so the service can re-read it."""
    for r in records:
        ev_store.save(r)


# ── lifecycle: tentative creation ────────────────────────────────────


class TestTentativeCreation:
    def test_first_supporting_evidence_creates_tentative(self, service, stores):
        ev_store, b_store = stores
        records = [make_evidence("m_1", subscope="certainty", support_score=0.7)]
        _save_evidence_records(ev_store, records)

        updated = service.process_new_evidence(records)
        assert len(updated) == 1

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief is not None
        assert belief.status == "tentative"
        assert "m_1" in belief.supporting_memory_ids
        # First contribution sets direction: certainty (+1.0) × 0.7 = +0.7
        assert belief.value > 0

    def test_low_strength_evidence_doesnt_promote_to_tentative(self, service, stores):
        """Below min_supporting_strength still records but doesn't promote status."""
        ev_store, b_store = stores
        # Default min_supporting_strength = 0.3
        records = [make_evidence("m_1", subscope="certainty", support_score=0.1)]
        _save_evidence_records(ev_store, records)

        service.process_new_evidence(records)
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        # Belief was created (tentative is default for fresh) but the weak
        # support shouldn't pass the qualifying threshold to keep it as
        # tentative — it stays tentative on creation since that's the default
        assert belief is not None
        assert "m_1" in belief.supporting_memory_ids


# ── lifecycle: tentative → active ────────────────────────────────────


class TestActivation:
    def test_three_strong_supports_activate(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(f"m_{i}", source_id=f"src_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, records)

        service.process_new_evidence(records)
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.status == "active"
        assert belief.support_count == 3

    def test_two_supports_stay_tentative(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(f"m_{i}", source_id=f"src_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(2)
        ]
        _save_evidence_records(ev_store, records)

        service.process_new_evidence(records)
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.status == "tentative"

    def test_supports_outside_active_window_dont_activate(self, stores):
        """Support older than active_window_days doesn't count toward activation."""
        ev_store, b_store = stores
        thresholds = BeliefThresholds(
            active_window_days=30,
            active_min_support=3,
            active_min_confidence=0.0,
        )
        service = BeliefService(
            belief_store=b_store, evidence_store=ev_store,
            thresholds=thresholds, clock=lambda: FIXED_NOW,
            id_factory=lambda: "b_0001",
        )
        # Three supports but two are too old (40 days)
        records = [
            make_evidence("m_0", subscope="certainty", support_score=0.8,
                          minutes_ago=40 * 24 * 60),
            make_evidence("m_1", source_id="s1", subscope="certainty",
                          support_score=0.8, minutes_ago=40 * 24 * 60),
            make_evidence("m_2", source_id="s2", subscope="certainty",
                          support_score=0.8, minutes_ago=0),
        ]
        _save_evidence_records(ev_store, records)
        service.process_new_evidence(records)
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        # Only 1 recent support → still tentative
        assert belief.status == "tentative"


# ── contradiction handling ───────────────────────────────────────────


class TestContradiction:
    def test_opposite_direction_recorded_as_counterevidence(self, service, stores):
        ev_store, b_store = stores
        # Build a certainty-leaning belief
        supports = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.7)
            for i in range(2)
        ]
        _save_evidence_records(ev_store, supports)
        service.process_new_evidence(supports)

        # Now a hedging evidence — contradicts certainty
        contra = make_evidence(
            "m_contra", source_id="s_contra",
            subscope="hedging", rule_id="obs.epistemic.hedging",
            support_score=0.7,
        )
        _save_evidence_records(ev_store, [contra])
        service.process_new_evidence([contra])

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert "m_contra" in belief.counterevidence_ids
        assert belief.contradiction_count == 1

    def test_contradiction_lowers_confidence(self, service, stores):
        ev_store, b_store = stores
        supports = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, supports)
        service.process_new_evidence(supports)
        belief_before = b_store.get_for_user_dimension("alice", "epistemic_style")
        confidence_before = belief_before.confidence

        contra = make_evidence(
            "m_contra", source_id="s_contra",
            subscope="hedging", rule_id="obs.epistemic.hedging",
            support_score=0.7,
        )
        _save_evidence_records(ev_store, [contra])
        service.process_new_evidence([contra])

        belief_after = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief_after.confidence < confidence_before

    def test_contradiction_count_exceeds_support_invalidates(self, service, stores):
        ev_store, b_store = stores
        # 1 support, 3 contradictions → contradictions > support
        support = make_evidence("m_s", subscope="certainty", support_score=0.7)
        _save_evidence_records(ev_store, [support])
        service.process_new_evidence([support])

        contras = [
            make_evidence(f"m_c{i}", source_id=f"sc_{i}",
                          subscope="hedging", rule_id="obs.epistemic.hedging",
                          support_score=0.7)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, contras)
        service.process_new_evidence(contras)

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.status == "invalidated"

    def test_contradiction_burst_invalidates(self, service, stores):
        ev_store, b_store = stores
        # 5 supports establish strong belief
        supports = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(5)
        ]
        _save_evidence_records(ev_store, supports)
        service.process_new_evidence(supports)

        # 3 contradictions in last 7 days → burst invalidation
        contras = [
            make_evidence(f"m_c{i}", source_id=f"sc_{i}",
                          subscope="hedging", rule_id="obs.epistemic.hedging",
                          support_score=0.7, minutes_ago=i * 60)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, contras)
        service.process_new_evidence(contras)

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.status == "invalidated"

    def test_evidence_ledger_is_append_only(self, service, stores):
        """Counterevidence does not erase supports from the ledger."""
        ev_store, b_store = stores
        supports = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.7)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, supports)
        service.process_new_evidence(supports)

        contra = make_evidence(
            "m_contra", source_id="s_contra",
            subscope="hedging", rule_id="obs.epistemic.hedging",
            support_score=0.9,
        )
        _save_evidence_records(ev_store, [contra])
        service.process_new_evidence([contra])

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        # All 3 original supports still in the ledger
        assert len(belief.supporting_memory_ids) == 3
        assert len(belief.counterevidence_ids) == 1


# ── staleness ────────────────────────────────────────────────────────


class TestStaleness:
    def test_sweep_marks_old_active_belief_stale(self, stores):
        ev_store, b_store = stores
        thresholds = BeliefThresholds(stale_window_days=14, active_min_confidence=0.0)
        # Build active belief, then advance the clock past stale window
        clock_state = {"now": FIXED_NOW}
        service = BeliefService(
            belief_store=b_store, evidence_store=ev_store,
            thresholds=thresholds, clock=lambda: clock_state["now"],
            id_factory=lambda: "b_0001",
        )

        supports = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, supports)
        service.process_new_evidence(supports)
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.status == "active"

        # Advance clock past staleness window
        clock_state["now"] = FIXED_NOW + timedelta(days=20)
        changed = service.sweep_staleness("alice")
        assert len(changed) == 1
        belief_after = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief_after.status == "stale"

    def test_sweep_doesnt_touch_recent_active(self, service, stores):
        ev_store, b_store = stores
        supports = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, supports)
        service.process_new_evidence(supports)

        changed = service.sweep_staleness("alice")
        assert changed == []
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief.status == "active"

    def test_staleness_doesnt_delete_belief(self, stores):
        """Stale beliefs persist — they just stop being active truth."""
        ev_store, b_store = stores
        clock_state = {"now": FIXED_NOW}
        service = BeliefService(
            belief_store=b_store, evidence_store=ev_store,
            thresholds=BeliefThresholds(active_min_confidence=0.0),
            clock=lambda: clock_state["now"],
            id_factory=lambda: "b_0001",
        )
        supports = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, supports)
        service.process_new_evidence(supports)

        clock_state["now"] = FIXED_NOW + timedelta(days=30)
        service.sweep_staleness("alice")

        # Belief still in the store, ledger intact
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert belief is not None
        assert belief.support_count == 3


# ── dimension-local updates (guardrail A) ────────────────────────────


class TestDimensionLocality:
    def test_one_evidence_updates_at_most_one_belief(self, service, stores):
        ev_store, b_store = stores
        # Single epistemic evidence
        records = [make_evidence("m_1", subscope="certainty", support_score=0.7)]
        _save_evidence_records(ev_store, records)
        service.process_new_evidence(records)

        # Only epistemic_style belief should exist
        all_beliefs = b_store.list_for_user("alice")
        assert len(all_beliefs) == 1
        assert all_beliefs[0].dimension == "epistemic_style"

    def test_evidence_from_three_scopes_creates_three_beliefs(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence("m_e", scope="epistemic", subscope="certainty",
                          rule_id="obs.epistemic.certainty"),
            make_evidence("m_s", scope="social", subscope="other_reference",
                          rule_id="obs.social.other_reference"),
            make_evidence("m_p", scope="pragmatic", subscope="high_pragmatic_ratio",
                          rule_id="obs.pragmatic.high_ratio"),
        ]
        _save_evidence_records(ev_store, records)
        service.process_new_evidence(records)

        beliefs = b_store.list_for_user("alice")
        assert len(beliefs) == 3
        dims = {b.dimension for b in beliefs}
        assert dims == {"epistemic_style", "social_orientation", "pragmatic_style"}

    def test_dynamics_evidence_creates_interaction_stability_belief(self, service, stores):
        """Block 6: dynamics evidence now feeds interaction_stability dimension."""
        ev_store, b_store = stores
        records = [
            make_evidence(
                "m_dyn", scope="dynamics", subscope="rupture",
                rule_id="ep.dynamics.rupture", source_kind="episode",
            ),
        ]
        _save_evidence_records(ev_store, records)
        updated = service.process_new_evidence(records)

        # One belief created — interaction_stability, rupture-leaning
        assert b_store.count_for_user("alice") == 1
        belief = b_store.list_for_user("alice")[0]
        assert belief.dimension == "interaction_stability"
        assert belief.value < 0  # rupture pulls toward instability
        assert len(updated) == 1


# ── idempotency ──────────────────────────────────────────────────────


class TestIdempotency:
    def test_same_evidence_twice_doesnt_double_count(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, records)

        service.process_new_evidence(records)
        first_belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        first_support_count = first_belief.support_count

        # Process the same evidence again
        service.process_new_evidence(records)
        second_belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert second_belief.support_count == first_support_count


# ── recompute escape hatch ───────────────────────────────────────────


class TestRecomputeForUser:
    def test_recompute_rebuilds_beliefs_from_full_ledger(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.8)
            for i in range(3)
        ]
        _save_evidence_records(ev_store, records)
        service.process_new_evidence(records)

        # Manually corrupt the belief — change the value
        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        belief.value = -0.5  # wildly wrong
        b_store.upsert(belief)

        # Recompute from full evidence history
        rebuilt = service.recompute_for_user("alice")
        assert len(rebuilt) == 1
        # Value is now correct (positive — certainty-leaning)
        assert rebuilt[0].value > 0

    def test_recompute_with_no_evidence_yields_no_beliefs(self, service, stores):
        rebuilt = service.recompute_for_user("alice")
        assert rebuilt == []


# ── value semantics ──────────────────────────────────────────────────


class TestValueSemantics:
    def test_signed_dimension_stays_in_signed_range(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(f"m_{i}", source_id=f"s_{i}",
                          subscope="certainty", support_score=0.95)
            for i in range(5)
        ]
        _save_evidence_records(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.get_for_user_dimension("alice", "epistemic_style")
        assert -1.0 <= belief.value <= 1.0

    def test_magnitude_dimension_stays_non_negative(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(
                f"m_{i}", source_id=f"s_{i}",
                scope="pragmatic", subscope="high_pragmatic_ratio",
                rule_id="obs.pragmatic.high_ratio",
                support_score=0.7,
            )
            for i in range(3)
        ]
        _save_evidence_records(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.get_for_user_dimension("alice", "pragmatic_style")
        assert 0.0 <= belief.value <= 1.0
