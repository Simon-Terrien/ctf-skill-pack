"""
Block 6 — belief explanations.

Locked:
  - on demand via belief_service.explain(belief_id)
  - structured dataclass, no LLM-generated prose
  - top supporting/counterevidence ranked by (support_score DESC,
    created_at DESC, memory_id DESC), capped at top_n (default 5)
  - superseded_count exposed for audit
  - NOT carried on RuntimeStateView
"""

from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from cms.l3.belief_explanation import BeliefExplanation
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
        belief_store=b_store, evidence_store=ev_store,
        clock=lambda: FIXED_NOW,
        id_factory=lambda: f"bel_{next(counter):04d}",
    )


def make_evidence(
    memory_id: str,
    *,
    user_id: str = "alice",
    scope: str = "epistemic",
    subscope: str = "certainty",
    rule_id: str = "obs.epistemic.certainty",
    source_id: str = "src",
    support_score: float = 0.8,
    created_at: datetime | None = None,
    supersedes: list[str] | None = None,
    context_key: str | None = None,
) -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=memory_id, user_id=user_id,
        created_at=created_at or FIXED_NOW,
        source_kind="observation", source_id=source_id, rule_id=rule_id,
        scope=scope, subscope=subscope,
        summary="x", support_score=support_score, relevance_score=1.0,
        supersedes=supersedes or [],
        context_key=context_key,
    )


def _save(store, records):
    for r in records:
        store.save(r)


# ── Basic shape ──────────────────────────────────────────────────────


class TestExplainShape:
    def test_explain_returns_explanation_for_existing_belief(self, service, stores):
        ev_store, b_store = stores
        records = [make_evidence("m1", source_id="o1")]
        _save(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.list_for_user("alice")[0]
        explanation = service.explain(belief.belief_id)

        assert isinstance(explanation, BeliefExplanation)
        assert explanation.belief_id == belief.belief_id
        assert explanation.dimension == belief.dimension
        assert explanation.status == belief.status
        assert explanation.value == belief.value

    def test_explain_returns_none_for_unknown_id(self, service):
        assert service.explain("does_not_exist") is None


# ── Top-N ranking ────────────────────────────────────────────────────


class TestTopNRanking:
    def test_supporting_ranked_by_support_score_desc(self, service, stores):
        ev_store, b_store = stores
        # 3 supports with different support_scores
        records = [
            make_evidence("m_low",  source_id="o_low",  support_score=0.5),
            make_evidence("m_mid",  source_id="o_mid",  support_score=0.7),
            make_evidence("m_high", source_id="o_high", support_score=0.9),
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.list_for_user("alice")[0]
        explanation = service.explain(belief.belief_id)

        # Highest support_score first
        assert explanation.top_supporting_memory_ids[0] == "m_high"
        assert explanation.top_supporting_memory_ids[-1] == "m_low"

    def test_top_n_cap_default_five(self, service, stores):
        ev_store, b_store = stores
        # 8 supports
        records = [
            make_evidence(
                f"m{i}", source_id=f"o{i}",
                support_score=0.5 + i * 0.05,
            )
            for i in range(8)
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.list_for_user("alice")[0]
        explanation = service.explain(belief.belief_id)

        assert len(explanation.top_supporting_memory_ids) == 5

    def test_top_n_explicit_override(self, service, stores):
        ev_store, b_store = stores
        records = [
            make_evidence(
                f"m{i}", source_id=f"o{i}",
                support_score=0.5 + i * 0.05,
            )
            for i in range(8)
        ]
        _save(ev_store, records)
        service.process_new_evidence(records)

        belief = b_store.list_for_user("alice")[0]
        explanation = service.explain(belief.belief_id, top_n=3)

        assert len(explanation.top_supporting_memory_ids) == 3


# ── Superseded count ─────────────────────────────────────────────────


class TestSupersededInExplanation:
    def test_superseded_count_excluded_from_active_but_reported(
        self, service, stores
    ):
        ev_store, b_store = stores
        # First 3 records — original support
        old = [
            make_evidence(
                f"m_old{i}", source_id=f"o_old{i}",
                created_at=FIXED_NOW - timedelta(days=60),
            )
            for i in range(3)
        ]
        # New record that supersedes them
        new = [
            make_evidence(
                "m_new", source_id="o_new",
                supersedes=[r.memory_id for r in old],
            ),
        ]
        _save(ev_store, old + new)
        service.process_new_evidence(old + new)

        belief = b_store.list_for_user("alice")[0]
        explanation = service.explain(belief.belief_id)

        # Active support count excludes superseded
        assert explanation.support_count == 1
        # But superseded_count reports them
        assert explanation.superseded_count == 3
        # Top supports only show active records
        assert "m_new" in explanation.top_supporting_memory_ids
        for old_id in [r.memory_id for r in old]:
            assert old_id not in explanation.top_supporting_memory_ids


# ── Counterevidence ──────────────────────────────────────────────────


class TestCounterevidenceInExplanation:
    def test_contradictions_show_in_top_counterevidence(self, service, stores):
        ev_store, b_store = stores

        # Establish certainty support
        supports = [
            make_evidence(f"m_s{i}", source_id=f"o_s{i}", support_score=0.9)
            for i in range(3)
        ]
        # File a hedging record that contradicts
        contras = [
            make_evidence(
                "m_h", source_id="o_h",
                subscope="hedging", rule_id="obs.epistemic.hedging",
                support_score=0.7,
            ),
        ]
        _save(ev_store, supports + contras)
        service.process_new_evidence(supports + contras)

        belief = b_store.list_for_user("alice")[0]
        explanation = service.explain(belief.belief_id)

        assert "m_h" in explanation.top_counterevidence_ids
        assert explanation.contradiction_count == 1
