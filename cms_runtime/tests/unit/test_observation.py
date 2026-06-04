"""Unit tests for L1Observation dataclass invariants."""

from datetime import datetime, timezone

import pytest

from cms.l1.observation import L1Observation


def _valid_kwargs(**overrides) -> dict:
    """Default-valid kwargs for L1Observation construction."""
    base = {
        "obs_id": "obs_001",
        "user_id": "user_alice",
        "session_id": "sess_001",
        "turn_id": "turn_0",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "raw_text": "The server is running.",
        "language": "en",
        "cms_real": [0.5, 0.7, 0.6],
        "cms_imag": [0.3, 0.4, 0.5],
        "temporal_phase": 0.0,
    }
    base.update(overrides)
    return base


class TestConstruction:
    def test_minimal_construction(self):
        obs = L1Observation(**_valid_kwargs())
        assert obs.obs_id == "obs_001"
        assert obs.cms_dim == 3

    def test_default_collections_are_empty(self):
        obs = L1Observation(**_valid_kwargs())
        assert obs.features == {}
        assert obs.tags == []
        assert obs.entities == []
        assert obs.quality == {}
        assert obs.metadata == {}

    def test_default_collections_are_independent(self):
        """Each instance gets its own collections (no shared default)."""
        a = L1Observation(**_valid_kwargs(obs_id="a"))
        b = L1Observation(**_valid_kwargs(obs_id="b"))
        a.tags.append("foo")
        assert b.tags == []


class TestInvariants:
    def test_cms_dimension_mismatch_rejected(self):
        with pytest.raises(ValueError, match="CMS dimension mismatch"):
            L1Observation(**_valid_kwargs(cms_real=[0.1, 0.2], cms_imag=[0.3]))

    def test_empty_obs_id_rejected(self):
        with pytest.raises(ValueError, match="obs_id"):
            L1Observation(**_valid_kwargs(obs_id=""))

    def test_empty_user_id_rejected(self):
        with pytest.raises(ValueError, match="user_id"):
            L1Observation(**_valid_kwargs(user_id=""))

    def test_empty_session_id_rejected(self):
        with pytest.raises(ValueError, match="session_id"):
            L1Observation(**_valid_kwargs(session_id=""))


class TestComplexConversion:
    def test_to_complex_roundtrip(self):
        obs = L1Observation(
            **_valid_kwargs(cms_real=[0.5, 0.7, 0.6], cms_imag=[0.3, 0.4, 0.5])
        )
        c = obs.to_complex()
        assert len(c) == 3
        assert c[0] == complex(0.5, 0.3)
        assert c[1] == complex(0.7, 0.4)
        assert c[2] == complex(0.6, 0.5)

    def test_to_complex_empty_dims(self):
        obs = L1Observation(**_valid_kwargs(cms_real=[], cms_imag=[]))
        assert obs.to_complex() == []
        assert obs.cms_dim == 0
