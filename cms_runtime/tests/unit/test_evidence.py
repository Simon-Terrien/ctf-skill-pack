"""Unit tests for MemoryEvidence dataclass."""

from datetime import datetime, timezone

import pytest

from cms.l3.evidence import CANONICAL_SCOPES, MemoryEvidence


def _valid_kwargs(**overrides) -> dict:
    base = {
        "memory_id": "mem_001",
        "user_id": "alice",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "source_kind": "observation",
        "source_id": "obs_042",
        "rule_id": "obs.epistemic.certainty",
        "scope": "epistemic",
        "subscope": "certainty",
        "summary": "utterance expressed high epistemic certainty",
        "support_score": 0.6,
        "relevance_score": 1.0,
    }
    base.update(overrides)
    return base


class TestConstruction:
    def test_minimal_construction(self):
        ev = MemoryEvidence(**_valid_kwargs())
        assert ev.memory_id == "mem_001"
        assert ev.scope == "epistemic"

    def test_defaults_are_empty_independent(self):
        a = MemoryEvidence(**_valid_kwargs(memory_id="a"))
        b = MemoryEvidence(**_valid_kwargs(memory_id="b"))
        a.tags.append("x")
        a.supersedes.append("m0")
        a.contradicted_by.append("m1")
        assert b.tags == []
        assert b.supersedes == []
        assert b.contradicted_by == []

    def test_contradiction_fields_default_empty_lists(self):
        ev = MemoryEvidence(**_valid_kwargs())
        assert ev.supersedes == []
        assert ev.contradicted_by == []
        assert isinstance(ev.supersedes, list)
        assert isinstance(ev.contradicted_by, list)


class TestInvariants:
    def test_empty_memory_id_rejected(self):
        with pytest.raises(ValueError, match="memory_id"):
            MemoryEvidence(**_valid_kwargs(memory_id=""))

    def test_empty_user_id_rejected(self):
        with pytest.raises(ValueError, match="user_id"):
            MemoryEvidence(**_valid_kwargs(user_id=""))

    def test_invalid_source_kind_rejected(self):
        with pytest.raises(ValueError, match="source_kind"):
            MemoryEvidence(**_valid_kwargs(source_kind="belief"))

    def test_empty_source_id_rejected(self):
        with pytest.raises(ValueError, match="source_id"):
            MemoryEvidence(**_valid_kwargs(source_id=""))

    def test_empty_rule_id_rejected(self):
        with pytest.raises(ValueError, match="rule_id"):
            MemoryEvidence(**_valid_kwargs(rule_id=""))

    def test_empty_scope_rejected(self):
        with pytest.raises(ValueError, match="scope"):
            MemoryEvidence(**_valid_kwargs(scope=""))

    def test_support_score_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="support_score"):
            MemoryEvidence(**_valid_kwargs(support_score=1.5))
        with pytest.raises(ValueError, match="support_score"):
            MemoryEvidence(**_valid_kwargs(support_score=-0.1))

    def test_relevance_score_out_of_range_rejected(self):
        with pytest.raises(ValueError, match="relevance_score"):
            MemoryEvidence(**_valid_kwargs(relevance_score=2.0))
        with pytest.raises(ValueError, match="relevance_score"):
            MemoryEvidence(**_valid_kwargs(relevance_score=-0.5))

    def test_boundary_scores_accepted(self):
        # 0.0 and 1.0 are valid
        MemoryEvidence(**_valid_kwargs(support_score=0.0, relevance_score=0.0))
        MemoryEvidence(**_valid_kwargs(support_score=1.0, relevance_score=1.0))


class TestIdempotencyKey:
    def test_key_is_tuple_of_identifiers(self):
        ev = MemoryEvidence(**_valid_kwargs())
        key = ev.idempotency_key
        assert key == ("alice", "observation", "obs_042", "obs.epistemic.certainty")

    def test_different_rules_produce_different_keys(self):
        a = MemoryEvidence(**_valid_kwargs(
            memory_id="a", rule_id="obs.epistemic.certainty"
        ))
        b = MemoryEvidence(**_valid_kwargs(
            memory_id="b", rule_id="obs.epistemic.hedging"
        ))
        assert a.idempotency_key != b.idempotency_key

    def test_same_rule_different_source_produces_different_keys(self):
        a = MemoryEvidence(**_valid_kwargs(memory_id="a", source_id="obs_1"))
        b = MemoryEvidence(**_valid_kwargs(memory_id="b", source_id="obs_2"))
        assert a.idempotency_key != b.idempotency_key

    def test_user_scoping_in_key(self):
        a = MemoryEvidence(**_valid_kwargs(memory_id="a", user_id="alice"))
        b = MemoryEvidence(**_valid_kwargs(memory_id="b", user_id="bob"))
        assert a.idempotency_key != b.idempotency_key


class TestCanonicalScopes:
    def test_canonical_scopes_contains_expected_set(self):
        assert CANONICAL_SCOPES == frozenset({
            "pragmatic", "epistemic", "social", "dynamics"
        })

    def test_dataclass_accepts_any_scope_string(self):
        """The dataclass itself does NOT police scope — the service does.

        This is intentional: it lets research code construct evidence
        with experimental scopes outside the store path.
        """
        ev = MemoryEvidence(**_valid_kwargs(scope="experimental_new_scope"))
        assert ev.scope == "experimental_new_scope"
