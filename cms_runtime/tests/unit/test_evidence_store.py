"""Unit tests for EvidenceStore."""

from datetime import datetime, timezone

import pytest

from cms.l3.evidence import MemoryEvidence
from cms.storage.evidence_store import EvidenceStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


@pytest.fixture
def backend():
    be = SQLiteBackend(":memory:")
    be.bootstrap_schema(FULL_SCHEMA_DDL)
    yield be
    be.close()


@pytest.fixture
def store(backend):
    return EvidenceStore(backend)


def make_evidence(
    memory_id: str = "mem_001",
    user_id: str = "alice",
    source_kind: str = "observation",
    source_id: str = "obs_001",
    rule_id: str = "obs.epistemic.certainty",
    scope: str = "epistemic",
    **overrides,
) -> MemoryEvidence:
    base = {
        "memory_id": memory_id,
        "user_id": user_id,
        "created_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "source_kind": source_kind,
        "source_id": source_id,
        "rule_id": rule_id,
        "scope": scope,
        "subscope": "certainty",
        "tags": ["observation_level"],
        "summary": "utterance expressed high epistemic certainty",
        "support_score": 0.6,
        "relevance_score": 1.0,
        "feature_snapshot": {"epistemic_certainty": 0.9},
        "supersedes": [],
        "contradicted_by": [],
        "metadata": {},
    }
    base.update(overrides)
    return MemoryEvidence(**base)


# ── CRUD ─────────────────────────────────────────────────────────────


class TestSaveAndGet:
    def test_save_and_retrieve_roundtrip(self, store):
        ev = make_evidence()
        store.save(ev)
        retrieved = store.get("mem_001")
        assert retrieved is not None
        assert retrieved.memory_id == ev.memory_id
        assert retrieved.source_kind == ev.source_kind
        assert retrieved.scope == ev.scope
        assert retrieved.support_score == ev.support_score
        assert retrieved.feature_snapshot == ev.feature_snapshot

    def test_get_nonexistent(self, store):
        assert store.get("missing") is None

    def test_save_many(self, store):
        records = [
            make_evidence(memory_id=f"mem_{i}", source_id=f"obs_{i}")
            for i in range(5)
        ]
        count = store.save_many(records)
        assert count == 5
        for i in range(5):
            assert store.get(f"mem_{i}") is not None

    def test_datetime_preserves_timezone(self, store):
        ev = make_evidence()
        store.save(ev)
        retrieved = store.get("mem_001")
        assert retrieved.created_at.tzinfo is not None
        assert retrieved.created_at == ev.created_at


# ── Idempotency ──────────────────────────────────────────────────────


class TestIdempotencyCheck:
    def test_has_evidence_for_returns_false_when_empty(self, store):
        assert store.has_evidence_for(
            user_id="alice",
            source_kind="observation",
            source_id="obs_1",
            rule_id="obs.epistemic.certainty",
        ) is False

    def test_has_evidence_for_returns_true_after_save(self, store):
        ev = make_evidence()
        store.save(ev)
        assert store.has_evidence_for(
            user_id="alice",
            source_kind="observation",
            source_id="obs_001",
            rule_id="obs.epistemic.certainty",
        ) is True

    def test_has_evidence_is_user_scoped(self, store):
        store.save(make_evidence(user_id="alice"))
        # Same source/rule but different user should NOT match
        assert store.has_evidence_for(
            user_id="bob",
            source_kind="observation",
            source_id="obs_001",
            rule_id="obs.epistemic.certainty",
        ) is False

    def test_has_evidence_different_rule_returns_false(self, store):
        store.save(make_evidence(rule_id="obs.epistemic.certainty"))
        assert store.has_evidence_for(
            user_id="alice",
            source_kind="observation",
            source_id="obs_001",
            rule_id="obs.epistemic.hedging",
        ) is False


class TestUniquenessBackstop:
    """Schema-level UNIQUE constraint protects against duplicate key insertion."""

    def test_duplicate_key_rejected_at_schema_level(self, store):
        import sqlite3
        ev1 = make_evidence(memory_id="mem_001")
        store.save(ev1)

        # Try to insert a *different* memory_id with the same idempotency key
        # This should fail at the UNIQUE constraint (not PRIMARY KEY level)
        ev2 = make_evidence(memory_id="mem_002")  # same user/source/rule, diff memory_id
        with pytest.raises(sqlite3.IntegrityError):
            store.save(ev2)


# ── Query surfaces ───────────────────────────────────────────────────


class TestListForUser:
    def test_returns_all_user_records(self, store):
        for i in range(3):
            store.save(make_evidence(
                memory_id=f"mem_{i}",
                source_id=f"obs_{i}",
            ))
        store.save(make_evidence(
            memory_id="bob_mem",
            user_id="bob",
            source_id="bob_obs",
        ))

        alice = store.list_for_user("alice")
        bob = store.list_for_user("bob")
        assert len(alice) == 3
        assert len(bob) == 1


class TestListForSource:
    def test_retrieves_all_evidence_for_one_observation(self, store):
        # Same observation fires two different rules
        store.save(make_evidence(
            memory_id="m1",
            source_id="obs_42",
            rule_id="obs.epistemic.certainty",
        ))
        store.save(make_evidence(
            memory_id="m2",
            source_id="obs_42",
            rule_id="obs.social.other_reference",
            scope="social",
            subscope="other_reference",
        ))
        # Different observation
        store.save(make_evidence(
            memory_id="m3",
            source_id="obs_99",
        ))

        results = store.list_for_source("alice", "observation", "obs_42")
        assert len(results) == 2
        assert {r.memory_id for r in results} == {"m1", "m2"}


class TestListByScope:
    def test_filters_by_scope(self, store):
        store.save(make_evidence(memory_id="e1", scope="epistemic"))
        store.save(make_evidence(
            memory_id="p1", source_id="obs_2",
            rule_id="obs.pragmatic.high_ratio",
            scope="pragmatic",
        ))
        store.save(make_evidence(
            memory_id="e2", source_id="obs_3",
            rule_id="obs.epistemic.hedging",
            scope="epistemic",
        ))

        epistemic = store.list_by_scope("alice", "epistemic")
        pragmatic = store.list_by_scope("alice", "pragmatic")
        assert len(epistemic) == 2
        assert len(pragmatic) == 1


# ── Delete ───────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_single(self, store):
        store.save(make_evidence())
        store.delete("mem_001")
        assert store.get("mem_001") is None

    def test_delete_for_user(self, store):
        for i in range(3):
            store.save(make_evidence(memory_id=f"a_{i}", source_id=f"o_{i}"))
        store.save(make_evidence(
            memory_id="b_1", user_id="bob", source_id="bob_obs"
        ))

        deleted = store.delete_for_user("alice")
        assert deleted == 3
        assert store.count_for_user("alice") == 0
        assert store.count_for_user("bob") == 1


# ── Serialization edge cases ─────────────────────────────────────────


class TestSerialization:
    def test_feature_snapshot_roundtrip(self, store):
        snapshot = {"certainty": 0.85, "hedging": 0.15, "valence": -0.2}
        ev = make_evidence(feature_snapshot=snapshot)
        store.save(ev)
        assert store.get("mem_001").feature_snapshot == snapshot

    def test_contradiction_lists_roundtrip(self, store):
        # Even though Block 3 never populates these, Block 5 will —
        # so the storage layer must roundtrip them correctly.
        ev = make_evidence(
            supersedes=["old_mem_1", "old_mem_2"],
            contradicted_by=["conflict_1"],
        )
        store.save(ev)
        retrieved = store.get("mem_001")
        assert retrieved.supersedes == ["old_mem_1", "old_mem_2"]
        assert retrieved.contradicted_by == ["conflict_1"]

    def test_null_subscope_roundtrip(self, store):
        ev = make_evidence(subscope=None)
        store.save(ev)
        assert store.get("mem_001").subscope is None

    def test_unicode_in_summary(self, store):
        ev = make_evidence(summary="l'utterance a exprimé une certitude élevée")
        store.save(ev)
        assert "élevée" in store.get("mem_001").summary
