"""Integration tests for BibiApp — runtime wiring, sessions, beliefs."""

from datetime import datetime, timedelta, timezone

import pytest

from bibi.app import BibiApp
from bibi.config import BibiConfig, SessionConfig, StorageConfig


@pytest.fixture
def fixed_clock():
    state = {"now": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)}
    def get_now():
        return state["now"]
    def advance(**kwargs):
        state["now"] = state["now"] + timedelta(**kwargs)
    get_now.advance = advance
    return get_now


@pytest.fixture
def app(tmp_path, fixed_clock):
    cfg = BibiConfig(
        storage=StorageConfig(db_path=tmp_path / "bibi_test.db"),
        session=SessionConfig(idle_seconds=300),
    )
    app = BibiApp(cfg, clock=fixed_clock)
    yield app, fixed_clock
    app.close()


# ── basic turn flow ──────────────────────────────────────────────────


class TestBasicTurn:
    def test_first_turn_creates_session(self, app):
        bibi, _ = app
        result = bibi.turn("pylo", "I am working on the security course today.")
        assert result.new_session is True
        assert result.session_id.startswith("sess_pylo_")
        assert result.observation_id

    def test_turn_files_evidence(self, app):
        bibi, _ = app
        result = bibi.turn(
            "pylo",
            "I am absolutely certain this approach is correct.",
        )
        # Certainty markers should produce an epistemic.certainty record
        assert len(result.new_evidence_ids) >= 1

    def test_turn_returns_state_view(self, app):
        bibi, _ = app
        result = bibi.turn("pylo", "Today I am writing notes.")
        assert result.state.user_id == "pylo"
        assert result.state.session_id == result.session_id


# ── session boundaries ──────────────────────────────────────────────


class TestSessionBoundaries:
    def test_quick_successive_turns_share_session(self, app):
        bibi, clock = app
        r1 = bibi.turn("pylo", "First turn.")
        clock.advance(seconds=30)
        r2 = bibi.turn("pylo", "Second turn within 5 minutes.")

        assert r1.session_id == r2.session_id
        assert r1.new_session is True
        assert r2.new_session is False

    def test_idle_gap_starts_new_session(self, app):
        bibi, clock = app
        r1 = bibi.turn("pylo", "First turn.")
        # Advance past the 5-minute idle window
        clock.advance(minutes=10)
        r2 = bibi.turn("pylo", "After a long idle break.")

        assert r1.session_id != r2.session_id
        assert r2.new_session is True

    def test_different_users_have_separate_sessions(self, app):
        bibi, _ = app
        r1 = bibi.turn("pylo", "Pylo's first turn.")
        r2 = bibi.turn("alice", "Alice's first turn.")

        assert r1.session_id != r2.session_id
        assert r1.new_session is True
        assert r2.new_session is True


# ── belief lifecycle ────────────────────────────────────────────────


class TestBeliefLifecycle:
    def test_repeated_certainty_creates_active_belief(self, app):
        bibi, clock = app
        for i in range(4):
            bibi.turn("pylo", f"I am absolutely certain about decision {i}.")
            clock.advance(seconds=30)

        state = bibi.inspect("pylo")
        active = state.active_beliefs_global
        assert any(b.dimension == "epistemic_style" for b in active)

    def test_scoped_belief_with_context_key(self, app):
        bibi, clock = app
        # Establish a research-context belief
        for i in range(4):
            bibi.turn(
                "pylo",
                f"Maybe this approach could work, perhaps {i}.",
                context_key="research",
            )
            clock.advance(seconds=30)

        state = bibi.inspect("pylo")
        scoped = state.active_beliefs_scoped + state.tentative_beliefs_scoped
        assert any(b.context_key == "research" for b in scoped)


# ── recompute ───────────────────────────────────────────────────────


class TestRecompute:
    def test_recompute_rebuilds_beliefs(self, app):
        bibi, clock = app
        for i in range(4):
            bibi.turn("pylo", f"I am certain about thing {i}.")
            clock.advance(seconds=30)

        before = bibi.inspect("pylo")
        n_active_before = len(before.active_beliefs_global)

        bibi.recompute_for_user("pylo")

        after = bibi.inspect("pylo")
        n_active_after = len(after.active_beliefs_global)

        # Recompute is deterministic — same evidence should produce
        # the same number of active beliefs
        assert n_active_after == n_active_before


# ── persistence ─────────────────────────────────────────────────────


class TestPersistence:
    def test_state_persists_across_app_instances(self, tmp_path, fixed_clock):
        cfg = BibiConfig(
            storage=StorageConfig(db_path=tmp_path / "persist.db"),
            session=SessionConfig(idle_seconds=300),
        )

        # First instance: file some turns
        with BibiApp(cfg, clock=fixed_clock) as a:
            a.turn("pylo", "I am certain about the design.")
            fixed_clock.advance(seconds=30)
            a.turn("pylo", "Definitely the right approach.")
            fixed_clock.advance(seconds=30)
            a.turn("pylo", "Absolutely confident in this.")

        # Second instance: should see the prior data
        with BibiApp(cfg, clock=fixed_clock) as b:
            state = b.inspect("pylo")
            # Beliefs are user-scoped so they survive the restart
            n_beliefs = (
                len(state.active_beliefs_global)
                + len(state.tentative_beliefs_global)
            )
            assert n_beliefs >= 1


# ── inspection ──────────────────────────────────────────────────────


class TestInspection:
    def test_inspect_unknown_user_returns_empty_state(self, app):
        bibi, _ = app
        state = bibi.inspect("nobody")
        assert len(state.active_beliefs_global) == 0
        assert len(state.tentative_beliefs_global) == 0

    def test_list_sessions_returns_recent_session_ids(self, app):
        bibi, clock = app
        bibi.turn("pylo", "Turn 1.")
        clock.advance(minutes=10)
        bibi.turn("pylo", "Turn 2.")
        clock.advance(minutes=10)
        bibi.turn("pylo", "Turn 3.")

        sessions = bibi.list_sessions("pylo")
        assert len(sessions) == 3
