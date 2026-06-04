"""Unit tests for ProfileBelief and dimension specs."""

from datetime import datetime, timezone

import pytest

from cms.l3.belief import (
    DIMENSION_SPECS,
    DimensionSpec,
    ProfileBelief,
    VALID_BELIEF_STATUSES,
    dimension_for_scope,
)


def _kwargs(**overrides) -> dict:
    base = {
        "belief_id": "b_001",
        "user_id": "alice",
        "dimension": "epistemic_style",
        "value": 0.6,
        "confidence": 0.5,
        "stability": 0.8,
        "status": "tentative",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


# ── construction ─────────────────────────────────────────────────────


class TestConstruction:
    def test_minimal_construction(self):
        b = ProfileBelief(**_kwargs())
        assert b.belief_id == "b_001"
        assert b.dimension == "epistemic_style"
        assert b.support_count == 0
        assert b.contradiction_count == 0

    def test_default_collections_independent(self):
        a = ProfileBelief(**_kwargs(belief_id="a"))
        c = ProfileBelief(**_kwargs(belief_id="c"))
        a.supporting_memory_ids.append("mem_1")
        a.counterevidence_ids.append("mem_2")
        a.metadata["k"] = "v"
        assert c.supporting_memory_ids == []
        assert c.counterevidence_ids == []
        assert c.metadata == {}

    def test_status_helpers(self):
        active = ProfileBelief(**_kwargs(status="active"))
        stale = ProfileBelief(**_kwargs(belief_id="b_2", status="stale"))
        invalid = ProfileBelief(**_kwargs(belief_id="b_3", status="invalidated"))
        assert active.is_active
        assert not active.is_stale
        assert stale.is_stale
        assert invalid.is_invalidated


# ── invariants ───────────────────────────────────────────────────────


class TestInvariants:
    def test_empty_belief_id_rejected(self):
        with pytest.raises(ValueError, match="belief_id"):
            ProfileBelief(**_kwargs(belief_id=""))

    def test_empty_user_id_rejected(self):
        with pytest.raises(ValueError, match="user_id"):
            ProfileBelief(**_kwargs(user_id=""))

    def test_unknown_dimension_rejected(self):
        with pytest.raises(ValueError, match="unknown dimension"):
            ProfileBelief(**_kwargs(dimension="my_made_up_dimension"))

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="invalid status"):
            ProfileBelief(**_kwargs(status="confirmed"))

    def test_value_out_of_range_signed_dim(self):
        # epistemic_style: [-1, +1]
        with pytest.raises(ValueError, match="value"):
            ProfileBelief(**_kwargs(dimension="epistemic_style", value=1.5))
        with pytest.raises(ValueError, match="value"):
            ProfileBelief(**_kwargs(dimension="epistemic_style", value=-1.5))

    def test_value_out_of_range_magnitude_dim(self):
        # pragmatic_style: [0, +1]
        with pytest.raises(ValueError, match="value"):
            ProfileBelief(**_kwargs(dimension="pragmatic_style", value=-0.1))
        with pytest.raises(ValueError, match="value"):
            ProfileBelief(**_kwargs(dimension="pragmatic_style", value=1.1))

    def test_confidence_out_of_range(self):
        with pytest.raises(ValueError, match="confidence"):
            ProfileBelief(**_kwargs(confidence=1.5))
        with pytest.raises(ValueError, match="confidence"):
            ProfileBelief(**_kwargs(confidence=-0.1))

    def test_stability_out_of_range(self):
        with pytest.raises(ValueError, match="stability"):
            ProfileBelief(**_kwargs(stability=1.5))

    def test_boundary_values_accepted(self):
        # epistemic_style: -1.0 and +1.0 are valid
        ProfileBelief(**_kwargs(dimension="epistemic_style", value=-1.0))
        ProfileBelief(**_kwargs(dimension="epistemic_style", value=1.0))
        # pragmatic_style: 0.0 and 1.0 are valid
        ProfileBelief(**_kwargs(dimension="pragmatic_style", value=0.0))
        ProfileBelief(**_kwargs(dimension="pragmatic_style", value=1.0))


# ── dimension specs ──────────────────────────────────────────────────


class TestDimensionSpecs:
    def test_block6_ships_four_dimensions(self):
        """Block 5 shipped 3 dimensions. Block 6 adds interaction_stability."""
        assert set(DIMENSION_SPECS) == {
            "epistemic_style", "social_orientation", "pragmatic_style",
            "interaction_stability",
        }

    def test_interaction_stability_reads_dynamics_scope(self):
        """Block 6 addition: dynamics evidence now feeds a belief dimension."""
        spec = DIMENSION_SPECS["interaction_stability"]
        assert spec.source_scope == "dynamics"
        assert spec.polarity == "signed"
        assert spec.value_min == -1.0
        assert spec.value_max == 1.0
        assert spec.subscope_directions["rupture"] == -1.0
        assert spec.subscope_directions["sustained_regime"] == +1.0

    def test_signed_dimensions_have_symmetric_range(self):
        for name in ("epistemic_style", "social_orientation"):
            spec = DIMENSION_SPECS[name]
            assert spec.value_min == -1.0
            assert spec.value_max == 1.0
            assert spec.polarity == "signed"

    def test_pragmatic_style_is_magnitude(self):
        spec = DIMENSION_SPECS["pragmatic_style"]
        assert spec.value_min == 0.0
        assert spec.value_max == 1.0
        assert spec.polarity == "magnitude"

    def test_subscope_directions_exist_for_all_block3_subscopes(self):
        """The subscope_directions registry must cover the actual rule subscopes."""
        epistemic = DIMENSION_SPECS["epistemic_style"]
        assert "certainty" in epistemic.subscope_directions
        assert "hedging" in epistemic.subscope_directions

        social = DIMENSION_SPECS["social_orientation"]
        assert "self_reference" in social.subscope_directions
        assert "other_reference" in social.subscope_directions

        pragmatic = DIMENSION_SPECS["pragmatic_style"]
        assert "high_pragmatic_ratio" in pragmatic.subscope_directions
        assert "sustained_pragmatic_density" in pragmatic.subscope_directions

    def test_signed_dimensions_have_opposing_subscope_directions(self):
        epistemic = DIMENSION_SPECS["epistemic_style"]
        # certainty positive, hedging negative
        assert epistemic.subscope_directions["certainty"] > 0
        assert epistemic.subscope_directions["hedging"] < 0

    def test_magnitude_dimension_has_only_positive_directions(self):
        pragmatic = DIMENSION_SPECS["pragmatic_style"]
        for direction in pragmatic.subscope_directions.values():
            assert direction > 0


# ── scope → dimension mapping ────────────────────────────────────────


class TestDimensionForScope:
    def test_scope_to_dimension_mapping(self):
        assert dimension_for_scope("epistemic") == "epistemic_style"
        assert dimension_for_scope("social") == "social_orientation"
        assert dimension_for_scope("pragmatic") == "pragmatic_style"

    def test_dynamics_scope_now_maps_to_interaction_stability(self):
        """Block 6 addition: dynamics evidence now maps to interaction_stability."""
        assert dimension_for_scope("dynamics") == "interaction_stability"

    def test_unknown_scope_returns_none(self):
        assert dimension_for_scope("nonexistent_scope") is None


# ── valid statuses ───────────────────────────────────────────────────


class TestStatuses:
    def test_four_statuses_defined(self):
        assert VALID_BELIEF_STATUSES == frozenset({
            "tentative", "active", "stale", "invalidated"
        })
