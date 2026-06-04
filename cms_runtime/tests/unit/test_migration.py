"""Unit tests for the legacy CMSPoint → L1Observation migration adapter."""

from dataclasses import dataclass
from datetime import datetime, timezone

from cms.migration.from_cms_point import cms_point_to_observation


@dataclass
class FakeCMSPoint:
    """Mirror of the research-line CMSPoint structure."""
    z1: complex
    z2: complex
    z3: complex
    t: float


class TestBasicConversion:
    def test_basic_fields_mapped(self):
        point = FakeCMSPoint(z1=0.5+0.3j, z2=0.7+0.4j, z3=0.6+0.5j, t=5.0)
        obs = cms_point_to_observation(
            point, user_id="alice", session_id="s1",
            turn_id="t5", raw_text="The server is running."
        )
        assert obs.user_id == "alice"
        assert obs.session_id == "s1"
        assert obs.turn_id == "t5"
        assert obs.raw_text == "The server is running."

    def test_complex_components_split_correctly(self):
        point = FakeCMSPoint(z1=0.5+0.3j, z2=0.7+0.4j, z3=0.6+0.5j, t=0.0)
        obs = cms_point_to_observation(point, user_id="alice", session_id="s1")
        assert obs.cms_real == [0.5, 0.7, 0.6]
        assert obs.cms_imag == [0.3, 0.4, 0.5]

    def test_turn_id_derived_from_t_when_omitted(self):
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=42.0)
        obs = cms_point_to_observation(point, user_id="alice", session_id="s1")
        assert obs.turn_id == "t_42"

    def test_obs_id_generated_when_omitted(self):
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=0.0)
        obs1 = cms_point_to_observation(point, user_id="alice", session_id="s1")
        obs2 = cms_point_to_observation(point, user_id="alice", session_id="s1")
        assert obs1.obs_id != obs2.obs_id  # different uuids


class TestHonestyOfMissingFields:
    """The migration must NOT fabricate data not present in CMSPoint."""

    def test_features_dict_empty_after_migration(self):
        point = FakeCMSPoint(z1=0.5+0.3j, z2=0.7+0.4j, z3=0.6+0.5j, t=0.0)
        obs = cms_point_to_observation(point, user_id="alice", session_id="s1")
        # CMSPoint has no feature dict — migration must leave it empty
        assert obs.features == {}

    def test_tags_empty_after_migration(self):
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=0.0)
        obs = cms_point_to_observation(point, user_id="alice", session_id="s1")
        assert obs.tags == []
        assert obs.entities == []

    def test_temporal_phase_zero_for_legacy(self):
        """Legacy CMSPoint has no phase encoding — must be 0, not faked."""
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=10.0)
        obs = cms_point_to_observation(point, user_id="alice", session_id="s1")
        assert obs.temporal_phase == 0.0

    def test_quality_marks_migrated(self):
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=0.0)
        obs = cms_point_to_observation(point, user_id="alice", session_id="s1")
        assert obs.quality.get("migrated") == 1.0


class TestMetadataRecording:
    def test_migration_source_recorded_automatically(self):
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=7.0)
        obs = cms_point_to_observation(point, user_id="alice", session_id="s1")
        assert "migration" in obs.metadata
        assert obs.metadata["migration"]["source"] == "research_line.CMSPoint"
        assert obs.metadata["migration"]["t"] == 7.0

    def test_user_metadata_preserved_alongside_migration_marker(self):
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=0.0)
        obs = cms_point_to_observation(
            point, user_id="alice", session_id="s1",
            metadata={"source_dataset": "enron", "batch": 42},
        )
        assert obs.metadata["source_dataset"] == "enron"
        assert obs.metadata["batch"] == 42
        assert "migration" in obs.metadata


class TestExplicitOverrides:
    def test_explicit_obs_id_used(self):
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=0.0)
        obs = cms_point_to_observation(
            point, user_id="alice", session_id="s1", obs_id="custom_id_001"
        )
        assert obs.obs_id == "custom_id_001"

    def test_explicit_created_at_used(self):
        point = FakeCMSPoint(z1=0+0j, z2=0+0j, z3=0+0j, t=0.0)
        ts = datetime(2025, 6, 15, 9, 30, 0, tzinfo=timezone.utc)
        obs = cms_point_to_observation(
            point, user_id="alice", session_id="s1", created_at=ts
        )
        assert obs.created_at == ts
