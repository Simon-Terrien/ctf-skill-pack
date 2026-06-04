"""Unit tests for BeliefStore."""

from datetime import datetime, timezone

import pytest

from cms.l3.belief import ProfileBelief
from cms.storage.belief_store import BeliefStore
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
    return BeliefStore(backend)


def make_belief(
    belief_id: str = "b_001",
    user_id: str = "alice",
    dimension: str = "epistemic_style",
    value: float = 0.6,
    status: str = "tentative",
    **overrides,
) -> ProfileBelief:
    base = {
        "belief_id": belief_id,
        "user_id": user_id,
        "dimension": dimension,
        "value": value,
        "confidence": 0.5,
        "stability": 0.8,
        "status": status,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "supporting_memory_ids": ["mem_1", "mem_2"],
        "counterevidence_ids": [],
        "metadata": {},
    }
    base.update(overrides)
    return ProfileBelief(**base)


# ── upsert and get ───────────────────────────────────────────────────


class TestUpsertAndGet:
    def test_upsert_and_retrieve(self, store):
        b = make_belief()
        store.upsert(b)
        retrieved = store.get("b_001")
        assert retrieved is not None
        assert retrieved.belief_id == "b_001"
        assert retrieved.dimension == "epistemic_style"
        assert retrieved.supporting_memory_ids == ["mem_1", "mem_2"]

    def test_get_nonexistent(self, store):
        assert store.get("missing") is None

    def test_upsert_replaces_existing(self, store):
        b = make_belief(value=0.3)
        store.upsert(b)
        b.value = 0.8
        b.confidence = 0.9
        store.upsert(b)
        retrieved = store.get("b_001")
        assert retrieved.value == 0.8
        assert retrieved.confidence == 0.9

    def test_datetime_preserves_timezone(self, store):
        b = make_belief()
        store.upsert(b)
        retrieved = store.get("b_001")
        assert retrieved.created_at.tzinfo is not None
        assert retrieved.created_at == b.created_at


# ── unique constraint on (user_id, dimension) ────────────────────────


class TestUniquenessConstraint:
    def test_two_beliefs_same_user_same_dimension_rejected(self, store):
        """Schema enforces one belief per (user, dimension)."""
        import sqlite3
        b1 = make_belief(belief_id="b_a", dimension="epistemic_style")
        store.upsert(b1)
        b2 = make_belief(belief_id="b_b", dimension="epistemic_style")
        # Different belief_id, same (user_id, dimension) — must fail
        with pytest.raises(sqlite3.IntegrityError):
            store.upsert(b2)

    def test_two_beliefs_different_dimensions_ok(self, store):
        store.upsert(make_belief(belief_id="b_a", dimension="epistemic_style"))
        store.upsert(make_belief(
            belief_id="b_b", dimension="social_orientation",
            value=0.3,
        ))
        assert store.count_for_user("alice") == 2

    def test_two_beliefs_different_users_same_dimension_ok(self, store):
        store.upsert(make_belief(belief_id="b_a", user_id="alice"))
        store.upsert(make_belief(belief_id="b_b", user_id="bob"))
        assert store.count_for_user("alice") == 1
        assert store.count_for_user("bob") == 1


# ── per-dimension lookup ─────────────────────────────────────────────


class TestGetForUserDimension:
    def test_returns_belief_when_present(self, store):
        store.upsert(make_belief(dimension="epistemic_style"))
        result = store.get_for_user_dimension("alice", "epistemic_style")
        assert result is not None
        assert result.dimension == "epistemic_style"

    def test_returns_none_when_absent(self, store):
        result = store.get_for_user_dimension("alice", "epistemic_style")
        assert result is None

    def test_user_scoped(self, store):
        store.upsert(make_belief(user_id="alice"))
        result = store.get_for_user_dimension("bob", "epistemic_style")
        assert result is None


# ── status filtering ─────────────────────────────────────────────────


class TestStatusFiltering:
    def test_list_active(self, store):
        store.upsert(make_belief(belief_id="b_a", dimension="epistemic_style", status="active"))
        store.upsert(make_belief(
            belief_id="b_b", dimension="social_orientation", status="tentative",
            value=0.3,
        ))
        store.upsert(make_belief(
            belief_id="b_c", dimension="pragmatic_style", status="stale",
            value=0.5,
        ))
        active = store.list_active("alice")
        assert len(active) == 1
        assert active[0].belief_id == "b_a"

    def test_list_tentative(self, store):
        store.upsert(make_belief(belief_id="b_a", dimension="epistemic_style", status="tentative"))
        store.upsert(make_belief(
            belief_id="b_b", dimension="social_orientation", status="active",
            value=0.3,
        ))
        tentative = store.list_tentative("alice")
        assert len(tentative) == 1
        assert tentative[0].belief_id == "b_a"

    def test_list_stale_and_invalidated(self, store):
        store.upsert(make_belief(belief_id="b_a", dimension="epistemic_style", status="stale"))
        store.upsert(make_belief(
            belief_id="b_b", dimension="social_orientation", status="invalidated",
            value=0.3,
        ))
        assert len(store.list_stale("alice")) == 1
        assert len(store.list_invalidated("alice")) == 1


# ── list and count for user ──────────────────────────────────────────


class TestListAndCount:
    def test_list_for_user(self, store):
        store.upsert(make_belief(belief_id="b_a", dimension="epistemic_style"))
        store.upsert(make_belief(
            belief_id="b_b", dimension="social_orientation", value=0.3,
        ))
        store.upsert(make_belief(belief_id="b_c", user_id="bob"))

        alice = store.list_for_user("alice")
        bob = store.list_for_user("bob")
        assert len(alice) == 2
        assert len(bob) == 1

    def test_count_for_user(self, store):
        for i, dim in enumerate(("epistemic_style", "social_orientation")):
            store.upsert(make_belief(
                belief_id=f"b_{i}", dimension=dim, value=0.3,
            ))
        assert store.count_for_user("alice") == 2
        assert store.count_for_user("nobody") == 0


# ── delete ───────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_single(self, store):
        store.upsert(make_belief())
        store.delete("b_001")
        assert store.get("b_001") is None

    def test_delete_for_user(self, store):
        for i, dim in enumerate(("epistemic_style", "social_orientation")):
            store.upsert(make_belief(
                belief_id=f"b_{i}", dimension=dim, value=0.3,
            ))
        deleted = store.delete_for_user("alice")
        assert deleted == 2
        assert store.count_for_user("alice") == 0


# ── serialization ────────────────────────────────────────────────────


class TestSerialization:
    def test_ledger_lists_roundtrip(self, store):
        b = make_belief(
            supporting_memory_ids=["mem_1", "mem_2", "mem_3"],
            counterevidence_ids=["mem_x"],
        )
        store.upsert(b)
        retrieved = store.get("b_001")
        assert retrieved.supporting_memory_ids == ["mem_1", "mem_2", "mem_3"]
        assert retrieved.counterevidence_ids == ["mem_x"]

    def test_metadata_roundtrip(self, store):
        b = make_belief(metadata={"note": "first belief", "version": 2})
        store.upsert(b)
        retrieved = store.get("b_001")
        assert retrieved.metadata == {"note": "first belief", "version": 2}
