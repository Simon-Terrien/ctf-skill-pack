"""
Block 6 — belief transition events.

Locked event types (six):
  - belief_tentative_created
  - belief_activated
  - belief_staled
  - belief_invalidated
  - belief_recomputed
  - belief_scoped_created

Tests verify:
  - events fire ONLY on real status transitions, not on every numeric tweak
  - scoped_created fires on first scoped lane instantiation
  - recomputed fires from recompute_for_user
  - staled fires from sweep_staleness
  - LoggingEventHandler is callable (smoke)
  - NullEventHandler is the default and drops everything
"""

from datetime import datetime, timedelta, timezone
from itertools import count

import pytest

from cms.l3.belief_events import (
    BeliefEvent,
    LoggingEventHandler,
    NullEventHandler,
)
from cms.l3.belief_service import BeliefService
from cms.l3.evidence import MemoryEvidence
from cms.storage.belief_store import BeliefStore
from cms.storage.evidence_store import EvidenceStore
from cms.storage.schema import FULL_SCHEMA_DDL
from cms.storage.sqlite import SQLiteBackend


FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class CollectingHandler:
    """Test handler that captures every event for inspection."""

    def __init__(self):
        self.events: list[BeliefEvent] = []

    def __call__(self, event: BeliefEvent) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.event_type for e in self.events]

    def filter(self, event_type: str) -> list[BeliefEvent]:
        return [e for e in self.events if e.event_type == event_type]


@pytest.fixture
def fixed_clock():
    state = {"now": FIXED_NOW}
    def get_now():
        return state["now"]
    def advance(**kwargs):
        state["now"] = state["now"] + timedelta(**kwargs)
    get_now.advance = advance
    return get_now


@pytest.fixture
def stack(fixed_clock):
    backend = SQLiteBackend(":memory:")
    backend.bootstrap_schema(FULL_SCHEMA_DDL)
    ev_store = EvidenceStore(backend)
    b_store = BeliefStore(backend)

    bel_counter = count(0)
    handler = CollectingHandler()

    bf_service = BeliefService(
        belief_store=b_store, evidence_store=ev_store,
        clock=fixed_clock,
        id_factory=lambda: f"bel_{next(bel_counter):04d}",
        event_handler=handler,
    )

    yield {
        "ev_store": ev_store, "b_store": b_store,
        "bf_service": bf_service, "handler": handler,
        "clock": fixed_clock,
    }
    backend.close()


def make_evidence(
    memory_id: str,
    *,
    user_id: str = "alice",
    scope: str = "epistemic",
    subscope: str = "certainty",
    rule_id: str = "obs.epistemic.certainty",
    source_id: str = "src",
    support_score: float = 0.8,
    context_key: str | None = None,
    created_at: datetime | None = None,
) -> MemoryEvidence:
    return MemoryEvidence(
        memory_id=memory_id, user_id=user_id,
        created_at=created_at or FIXED_NOW,
        source_kind="observation", source_id=source_id, rule_id=rule_id,
        scope=scope, subscope=subscope,
        summary="x", support_score=support_score, relevance_score=1.0,
        context_key=context_key,
    )


def _save(store, records):
    for r in records:
        store.save(r)


# ── tentative_created ────────────────────────────────────────────────


class TestTentativeCreatedEvent:
    def test_first_evidence_emits_tentative_created(self, stack):
        ev_store, bf_service, handler = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
        )
        records = [make_evidence("m1", source_id="o1")]
        _save(ev_store, records)
        bf_service.process_new_evidence(records)

        tentative_events = handler.filter("belief_tentative_created")
        assert len(tentative_events) == 1
        e = tentative_events[0]
        assert e.dimension == "epistemic_style"
        assert e.context_key is None
        assert e.status_before is None
        assert e.status_after == "tentative"
        assert "m1" in e.triggered_by_evidence_ids


# ── activated ────────────────────────────────────────────────────────


class TestActivatedEvent:
    def test_promotion_to_active_emits_activated(self, stack):
        ev_store, bf_service, handler = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
        )
        records = [
            make_evidence(f"m{i}", source_id=f"o{i}", support_score=0.9)
            for i in range(4)
        ]
        _save(ev_store, records)
        bf_service.process_new_evidence(records)

        activated = handler.filter("belief_activated")
        assert len(activated) == 1
        e = activated[0]
        assert e.status_before == "tentative"
        assert e.status_after == "active"


# ── invalidated ──────────────────────────────────────────────────────


class TestInvalidatedEvent:
    def test_contradiction_burst_emits_invalidated(self, stack):
        ev_store, bf_service, handler = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
        )
        # Establish active
        supports = [
            make_evidence(f"m{i}", source_id=f"o{i}", support_score=0.9)
            for i in range(4)
        ]
        _save(ev_store, supports)
        bf_service.process_new_evidence(supports)

        # Burst of contradictions
        contras = [
            make_evidence(
                f"h{i}", subscope="hedging",
                rule_id="obs.epistemic.hedging",
                source_id=f"oh{i}", support_score=0.9,
            )
            for i in range(5)
        ]
        _save(ev_store, contras)
        bf_service.process_new_evidence(contras)

        invalidated = handler.filter("belief_invalidated")
        assert len(invalidated) >= 1
        assert invalidated[0].status_after == "invalidated"


# ── staled ───────────────────────────────────────────────────────────


class TestStaledEvent:
    def test_sweep_emits_staled_event(self, stack):
        ev_store, bf_service, handler, clock = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
            stack["clock"],
        )
        # Establish active
        records = [
            make_evidence(f"m{i}", source_id=f"o{i}", support_score=0.9)
            for i in range(4)
        ]
        _save(ev_store, records)
        bf_service.process_new_evidence(records)

        # Advance past staleness window (default 14 days)
        clock.advance(days=20)

        # Clear non-stale events, then sweep
        handler.events.clear()
        bf_service.sweep_staleness("alice")

        staled = handler.filter("belief_staled")
        assert len(staled) == 1
        e = staled[0]
        assert e.status_before == "active"
        assert e.status_after == "stale"


# ── scoped_created ───────────────────────────────────────────────────


class TestScopedCreatedEvent:
    def test_first_scoped_evidence_emits_scoped_created(self, stack):
        ev_store, bf_service, handler = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
        )
        records = [
            make_evidence("m1", source_id="o1", context_key="research"),
        ]
        _save(ev_store, records)
        bf_service.process_new_evidence(records)

        scoped = handler.filter("belief_scoped_created")
        assert len(scoped) == 1
        assert scoped[0].context_key == "research"

    def test_global_belief_does_not_emit_scoped_created(self, stack):
        ev_store, bf_service, handler = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
        )
        records = [make_evidence("m1", source_id="o1", context_key=None)]
        _save(ev_store, records)
        bf_service.process_new_evidence(records)

        assert handler.filter("belief_scoped_created") == []


# ── recomputed ───────────────────────────────────────────────────────


class TestRecomputedEvent:
    def test_recompute_for_user_emits_recomputed(self, stack):
        ev_store, bf_service, handler = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
        )
        records = [
            make_evidence(f"m{i}", source_id=f"o{i}", support_score=0.9)
            for i in range(4)
        ]
        _save(ev_store, records)
        bf_service.process_new_evidence(records)

        handler.events.clear()
        bf_service.recompute_for_user("alice")

        recomputed = handler.filter("belief_recomputed")
        assert len(recomputed) >= 1


# ── transition discipline ────────────────────────────────────────────


class TestTransitionDiscipline:
    def test_no_event_for_idempotent_replay(self, stack):
        """Replaying the same evidence should not re-emit transitions."""
        ev_store, bf_service, handler = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
        )
        records = [make_evidence("m1", source_id="o1")]
        _save(ev_store, records)
        bf_service.process_new_evidence(records)

        events_before = len(handler.events)
        # Replay — same memory_id, should be skipped by idempotency
        bf_service.process_new_evidence(records)

        # Idempotency means we MAY get a same-status update with no
        # transition; either way, no NEW *_created or *_activated.
        new_creates = [
            e for e in handler.events[events_before:]
            if e.event_type in {"belief_tentative_created", "belief_activated"}
        ]
        assert new_creates == []

    def test_no_event_for_minor_numeric_update(self, stack):
        """Adding more support that doesn't change status → no event."""
        ev_store, bf_service, handler = (
            stack["ev_store"], stack["bf_service"], stack["handler"],
        )
        # First record → tentative_created
        first = [make_evidence("m1", source_id="o1", support_score=0.5)]
        _save(ev_store, first)
        bf_service.process_new_evidence(first)
        events_after_first = list(handler.events)

        # A second tentative-level support — no status change
        second = [make_evidence("m2", source_id="o2", support_score=0.5)]
        _save(ev_store, second)
        bf_service.process_new_evidence(second)

        new_events = handler.events[len(events_after_first):]
        # No new *_created or *_activated should fire
        new_transitions = [
            e for e in new_events
            if e.event_type in {
                "belief_tentative_created", "belief_activated",
                "belief_staled", "belief_invalidated",
            }
        ]
        assert new_transitions == []


# ── handler smoke tests ──────────────────────────────────────────────


class TestHandlerSmoke:
    def test_null_handler_is_default(self):
        backend = SQLiteBackend(":memory:")
        backend.bootstrap_schema(FULL_SCHEMA_DDL)
        try:
            ev_store = EvidenceStore(backend)
            b_store = BeliefStore(backend)
            service = BeliefService(b_store, ev_store)
            assert isinstance(service._event_handler, NullEventHandler)
        finally:
            backend.close()

    def test_logging_handler_is_callable(self):
        handler = LoggingEventHandler()
        # Should not raise
        handler(BeliefEvent(
            event_type="belief_tentative_created",
            timestamp=FIXED_NOW,
            belief_id="bel_x", user_id="alice",
            dimension="epistemic_style", context_key=None,
            status_before=None, status_after="tentative",
            triggered_by_evidence_ids=["m1"],
        ))
