"""
Block 6 — four-case matrix for dimensions × context_key.

Required by the locked contract: explicitly cover every combination of
{scope-pure, cross-scope} × {global, scoped} so that no case has a bug
hidden by the others.

  - scope-pure global   → epistemic_style with context_key=None
  - scope-pure scoped   → epistemic_style with context_key="research"
  - cross-scope global  → interaction_stability with context_key=None
  - cross-scope scoped  → interaction_stability with context_key="research"

"Cross-scope" here means a dimension that consumes a scope other than
the one it's named for. interaction_stability reads `dynamics` evidence
which is filed by episode-level rules — that's what makes it cross-scope.
"""

from datetime import datetime, timezone
from itertools import count

import pytest

from cms.l3.belief_service import BeliefService
from cms.l3.evidence import MemoryEvidence
from cms.storage.belief_store import BeliefStore
from cms.storage.evidence_store import EvidenceStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


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
        id_factory=lambda: f"bel_{next(counter):04d}",
    )


def make_evidence(
    memory_id: str,
    *,
    user_id: str = "alice",
    scope: str,
    subscope: str,
    rule_id: str,
    source_kind: str = "observation",
    source_id: str = "src",
    support_score: float = 0.8,
    context_key: str | None = None,
) -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=memory_id, user_id=user_id, created_at=FIXED_NOW,
        source_kind=source_kind, source_id=source_id, rule_id=rule_id,
        scope=scope, subscope=subscope,
        summary="x", support_score=support_score, relevance_score=1.0,
        context_key=context_key,
    )


def _save(store, records):
    for r in records:
        store.save(r)


# ── Case 1: scope-pure global ────────────────────────────────────────


class TestScopePureGlobal:
    def test_creates_global_epistemic_belief(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(
                "m1", scope="epistemic", subscope="certainty",
                rule_id="obs.epistemic.certainty",
                source_id="o1", context_key=None,
            ),
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key=None,
        )
        assert belief is not None
        assert belief.is_global
        assert belief.context_key is None
        assert belief.dimension == "epistemic_style"


# ── Case 2: scope-pure scoped ────────────────────────────────────────


class TestScopePureScoped:
    def test_creates_scoped_epistemic_belief(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(
                "m1", scope="epistemic", subscope="certainty",
                rule_id="obs.epistemic.certainty",
                source_id="o1", context_key="research",
            ),
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key="research",
        )
        assert belief is not None
        assert belief.is_scoped
        assert belief.context_key == "research"

        # And global lane is empty
        global_belief = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key=None,
        )
        assert global_belief is None


# ── Case 3: cross-scope global ───────────────────────────────────────


class TestCrossScopeGlobal:
    def test_creates_global_interaction_stability_from_dynamics(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(
                "m1", scope="dynamics", subscope="rupture",
                rule_id="ep.dynamics.rupture",
                source_kind="episode", source_id="e1",
                context_key=None,
            ),
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.get_for_user_dimension(
            "alice", "interaction_stability", context_key=None,
        )
        assert belief is not None
        assert belief.is_global
        assert belief.dimension == "interaction_stability"
        # Rupture pulls toward instability (-1 direction)
        assert belief.value < 0


# ── Case 4: cross-scope scoped ───────────────────────────────────────


class TestCrossScopeScoped:
    def test_creates_scoped_interaction_stability_from_dynamics(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(
                "m1", scope="dynamics", subscope="sustained_regime",
                rule_id="ep.dynamics.sustained",
                source_kind="episode", source_id="e1",
                context_key="ops",
            ),
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.get_for_user_dimension(
            "alice", "interaction_stability", context_key="ops",
        )
        assert belief is not None
        assert belief.is_scoped
        assert belief.context_key == "ops"
        # Sustained regime pulls toward stability (+1 direction)
        assert belief.value > 0


# ── Coexistence (Guardrail B) ────────────────────────────────────────


class TestGlobalScopedCoexistence:
    """Per Guardrail B: global and scoped beliefs coexist with no
    implicit reconciliation."""

    def test_global_and_scoped_same_dimension_coexist(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(
                "m_g", scope="epistemic", subscope="certainty",
                rule_id="obs.epistemic.certainty",
                source_id="o_g", context_key=None,
            ),
            make_evidence(
                "m_r", scope="epistemic", subscope="hedging",
                rule_id="obs.epistemic.hedging",
                source_id="o_r", context_key="research",
            ),
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        global_b = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key=None,
        )
        scoped_b = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key="research",
        )

        assert global_b is not None
        assert scoped_b is not None
        assert global_b.belief_id != scoped_b.belief_id
        # Global shows certainty (+), scoped shows hedging (-) — divergent
        assert global_b.value > 0
        assert scoped_b.value < 0

    def test_scoped_belief_does_not_affect_global(self, service, stores):
        """Filing scoped evidence must not write to the global lane."""
        ev_store, b_store = stores

        # First establish a global belief
        global_records = [
            make_evidence(
                f"m_g{i}", scope="epistemic", subscope="certainty",
                rule_id="obs.epistemic.certainty",
                source_id=f"o_g{i}", context_key=None,
                support_score=0.9,
            )
            for i in range(3)
        ]
        _save(ev_store, global_records)
        service.process_new_evidence(global_records)

        global_before = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key=None,
        )
        snapshot_before = (
            global_before.value, global_before.confidence,
            tuple(global_before.supporting_memory_ids),
        )

        # Now file a scoped record that would contradict if it leaked
        scoped_records = [
            make_evidence(
                "m_s", scope="epistemic", subscope="hedging",
                rule_id="obs.epistemic.hedging",
                source_id="o_s", context_key="research",
                support_score=0.9,
            ),
        ]
        _save(ev_store, scoped_records)
        service.process_new_evidence(scoped_records)

        global_after = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key=None,
        )
        snapshot_after = (
            global_after.value, global_after.confidence,
            tuple(global_after.supporting_memory_ids),
        )

        assert snapshot_before == snapshot_after, (
            "scoped evidence leaked into the global belief"
        )

    def test_two_distinct_scoped_lanes_do_not_interfere(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(
                "m_r", scope="epistemic", subscope="certainty",
                rule_id="obs.epistemic.certainty",
                source_id="o_r", context_key="research",
            ),
            make_evidence(
                "m_o", scope="epistemic", subscope="hedging",
                rule_id="obs.epistemic.hedging",
                source_id="o_o", context_key="ops",
            ),
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        research_b = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key="research",
        )
        ops_b = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key="ops",
        )

        assert research_b is not None
        assert ops_b is not None
        assert research_b.belief_id != ops_b.belief_id
        assert research_b.value > 0
        assert ops_b.value < 0
