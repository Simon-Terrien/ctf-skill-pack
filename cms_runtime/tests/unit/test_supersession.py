"""
Block 6 — filing-time supersession.

Locked semantics:
  - supersession at filing time, not recompute time
  - lane = (user_id, rule_id, context_key); strict (None and "research" differ)
  - default 30-day window; configurable via supersession_window_days
  - superseded records remain in the audit trail
  - belief recompute excludes superseded support from primary counting
  - supersession is replacement, not contradiction (same direction continues)
"""

from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from cms.l1.observation import L1Observation
from cms.l3.belief_service import BeliefService
from cms.l3.service import EvidenceService
from cms.storage.belief_store import BeliefStore
from cms.storage.evidence_store import EvidenceStore
from cms.storage.observation_store import ObservationStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


@pytest.fixture
def fixed_clock():
    state = {"now": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)}
    def get_now():
        return state["now"]
    def advance(**delta_kwargs):
        state["now"] = state["now"] + timedelta(**delta_kwargs)
    get_now.advance = advance
    get_now.set = lambda dt: state.update(now=dt)
    return get_now


@pytest.fixture
def stack(fixed_clock):
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    obs_store = ObservationStore(backend)
    ev_store = EvidenceStore(backend)
    b_store = BeliefStore(backend)

    obs_counter = count(0)
    ev_counter = count(0)
    bel_counter = count(0)

    ev_service = EvidenceService(
        store=ev_store,
        clock=fixed_clock,
        id_factory=lambda: f"mem_{next(ev_counter):04d}",
    )
    bf_service = BeliefService(
        belief_store=b_store, evidence_store=ev_store,
        clock=fixed_clock,
        id_factory=lambda: f"bel_{next(bel_counter):04d}",
    )

    yield {
        "obs_store": obs_store, "ev_store": ev_store, "b_store": b_store,
        "ev_service": ev_service, "bf_service": bf_service,
        "clock": fixed_clock,
        "obs_counter": obs_counter,
    }
    backend.close()


class TriggeringExtractor:
    def extract_sentence_features(self, sentence):
        f = {
            "semantic_density": 0.5, "pragmatic_load": 0.3,
            "epistemic_certainty": 0.5, "temporal_orientation": 0.5,
            "topic_concreteness": 0.5, "intent_direction": 0.5,
        }
        if sentence.startswith("CERT:"):
            f["epistemic_certainty"] = 0.95
        elif sentence.startswith("HEDGE:"):
            f["epistemic_certainty"] = 0.1
        return f


def _make_obs(stack, turn_idx: int, text: str, *, session_id: str = "s1") -> L1Observation:
    """Build a fresh observation at the current clock time for the given session."""
    from cms.l1.adapter import LegacyExtractorAdapter
    from cms.l1.service import ObservationService

    adapter = LegacyExtractorAdapter(TriggeringExtractor())
    obs_service = ObservationService(
        adapter=adapter, store=stack["obs_store"],
        clock=stack["clock"],
        id_factory=lambda: f"obs_{next(stack['obs_counter']):04d}",
    )
    return obs_service.ingest(
        user_id="alice", session_id=session_id,
        turn_id=f"t{turn_idx}", text=text,
    )


# ── Lane-aware filing supersession ──────────────────────────────────


class TestFilingTimeSupersession:
    def test_old_same_lane_record_gets_superseded_by_new(self, stack):
        """A new record older than 30 days in the same (user, rule, ctx)
        lane gets recorded in the new record's `supersedes` list."""
        ev_service = stack["ev_service"]
        ev_store = stack["ev_store"]
        clock = stack["clock"]

        # Old certainty observation
        old_obs = _make_obs(stack, 0, "CERT: long ago.")
        old_records = ev_service.file_from_observation(old_obs, context_key=None)
        assert len(old_records) >= 1
        certainty_old = next(
            r for r in old_records if r.rule_id == "obs.epistemic.certainty"
        )

        # Advance clock past the supersession window (30d default + buffer)
        clock.advance(days=35)

        # New certainty observation in same lane (same user, rule, context_key=None)
        new_obs = _make_obs(stack, 1, "CERT: today.")
        new_records = ev_service.file_from_observation(new_obs, context_key=None)
        certainty_new = next(
            r for r in new_records if r.rule_id == "obs.epistemic.certainty"
        )

        # The new record's supersedes should reference the old one
        assert certainty_old.memory_id in certainty_new.supersedes

        # Old record still in store (audit history preserved)
        retrieved_old = ev_store.get(certainty_old.memory_id)
        assert retrieved_old is not None

    def test_recent_same_lane_record_not_superseded(self, stack):
        """Within the supersession window, prior records are NOT superseded."""
        ev_service = stack["ev_service"]
        clock = stack["clock"]

        old_obs = _make_obs(stack, 0, "CERT: yesterday.")
        old_records = ev_service.file_from_observation(old_obs, context_key=None)
        certainty_old = next(
            r for r in old_records if r.rule_id == "obs.epistemic.certainty"
        )

        # Advance just 5 days — well under 30-day window
        clock.advance(days=5)

        new_obs = _make_obs(stack, 1, "CERT: today.")
        new_records = ev_service.file_from_observation(new_obs, context_key=None)
        certainty_new = next(
            r for r in new_records if r.rule_id == "obs.epistemic.certainty"
        )

        # Within the window — no supersession
        assert certainty_old.memory_id not in certainty_new.supersedes
        assert certainty_new.supersedes == []

    def test_different_context_lanes_do_not_supersede(self, stack):
        """Lane awareness: global and scoped lanes are distinct."""
        ev_service = stack["ev_service"]
        clock = stack["clock"]

        # File in global lane
        old_obs = _make_obs(stack, 0, "CERT: global lane.")
        old_records = ev_service.file_from_observation(old_obs, context_key=None)
        certainty_global = next(
            r for r in old_records if r.rule_id == "obs.epistemic.certainty"
        )

        # Advance past window
        clock.advance(days=35)

        # File in scoped lane — should NOT supersede the global record
        new_obs = _make_obs(stack, 1, "CERT: scoped lane.", session_id="s2")
        new_records = ev_service.file_from_observation(
            new_obs, context_key="research",
        )
        certainty_scoped = next(
            r for r in new_records if r.rule_id == "obs.epistemic.certainty"
        )

        assert certainty_global.memory_id not in certainty_scoped.supersedes
        assert certainty_scoped.supersedes == []

    def test_different_rule_ids_do_not_supersede(self, stack):
        """Lane awareness: certainty and hedging are different rules."""
        ev_service = stack["ev_service"]
        clock = stack["clock"]

        old_obs = _make_obs(stack, 0, "CERT: confident.")
        old_records = ev_service.file_from_observation(old_obs, context_key=None)
        certainty_old = next(
            r for r in old_records if r.rule_id == "obs.epistemic.certainty"
        )

        clock.advance(days=35)

        # Different rule (hedging) — different lane, no supersession
        new_obs = _make_obs(stack, 1, "HEDGE: maybe.")
        new_records = ev_service.file_from_observation(new_obs, context_key=None)
        hedging_new = next(
            r for r in new_records if r.rule_id == "obs.epistemic.hedging"
        )

        assert certainty_old.memory_id not in hedging_new.supersedes


# ── Recompute excludes superseded ────────────────────────────────────


class TestRecomputeExcludesSuperseded:
    def test_belief_recompute_excludes_superseded_support(self, stack):
        """Superseded records remain in the ledger but don't count toward
        primary value/confidence/threshold computation."""
        ev_service = stack["ev_service"]
        bf_service = stack["bf_service"]
        b_store = stack["b_store"]
        clock = stack["clock"]

        # File 4 old certainty records to establish active belief
        old_records = []
        for i in range(4):
            obs = _make_obs(stack, i, f"CERT: old {i}.")
            new = ev_service.file_from_observation(obs, context_key=None)
            old_records.extend(new)
            clock.advance(hours=1)
        bf_service.process_new_evidence(old_records)

        belief_before = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key=None,
        )
        assert belief_before is not None
        assert belief_before.status == "active"

        # Advance past supersession window
        clock.advance(days=35)

        # File a new certainty record — should supersede the old ones
        new_obs = _make_obs(stack, 100, "CERT: fresh.")
        new_records = ev_service.file_from_observation(new_obs, context_key=None)
        bf_service.process_new_evidence(new_records)

        belief_after = b_store.get_for_user_dimension(
            "alice", "epistemic_style", context_key=None,
        )

        # Old supports remain in supporting_memory_ids (audit chain)
        # but superseded count should reflect them
        superseded_count = belief_after.metadata.get("superseded_support_count", 0)
        assert superseded_count >= 4, (
            f"expected >=4 superseded, got {superseded_count}; "
            f"supersedes on new record: {new_records[0].supersedes}"
        )
