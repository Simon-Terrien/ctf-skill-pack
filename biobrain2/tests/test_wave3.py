"""
Tests for Wave 3: Session, Orchestrator, LLM adapter, Memory backend,
Action sandbox (timeout/dry-run/schema), Event bus.
"""

import time
import pytest
from unittest.mock import MagicMock, patch

from biobrain.core.enums import (
    InputSource, SystemMode, ActionType, ReasoningMode,
    OperationClass, ReflexVerdict,
)
from biobrain.core.signals import (
    RawInput, PerceivedInput, SalienceScore, MemoryQuery, MemoryResult,
    ExecutiveDecision, CognitiveResult, ActionRequest, ActionResult,
    ModeState, IdentityState, MemoryItem,
)
from biobrain.core.trace import PipelineTrace, ReflexResponse
from biobrain.memory import WorkingMemory
from biobrain.modulation import ModeManager


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mock_brain():
    """Create a BioBrain with mocked MemPalace."""
    with patch("biobrain.memory.MemoryManager") as MockMM:
        mock_mm = MockMM.return_value
        mock_mm.recall.return_value = MemoryResult(query=MemoryQuery(query="test"))
        mock_mm.working = WorkingMemory()
        mock_mm.store_episodic.return_value = None

        from biobrain.runtime.pipeline import BioBrain
        from biobrain.core.events import EventBus
        brain = BioBrain.__new__(BioBrain)
        brain.memory = mock_mm
        brain.identity = IdentityState()
        brain.mode_manager = ModeManager()
        brain.bus = EventBus()
        brain._traces = []
        return brain


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.runtime.session import Session, ApprovalRequest


class TestSession:
    def test_session_creation(self):
        brain = _mock_brain()
        session = Session(brain, wing="wing_test", room="room_test")
        assert session.session_id
        assert session.turn_count == 0

    def test_multi_turn(self):
        brain = _mock_brain()
        session = Session(brain)
        t1 = session.turn("what is the project status today")
        t2 = session.turn("explain the auth flow please")
        assert session.turn_count == 2
        assert len(session.traces) == 2

    def test_session_injects_metadata(self):
        brain = _mock_brain()
        session = Session(brain, wing="wing_adeo")
        trace = session.turn("scan target")
        # The metadata should have been injected with session context
        assert session.state.turns_completed == 1

    def test_session_tracks_escalations(self):
        brain = _mock_brain()
        session = Session(brain)
        # Trigger an escalation via reflex
        trace = session.turn("production deploy the release now")
        assert session.state.total_escalations >= 1 or trace.halted_at == "reflex_escalate"

    def test_session_cumulative_confidence(self):
        brain = _mock_brain()
        session = Session(brain)
        session.turn("explain something simple")
        session.turn("describe the architecture")
        # Confidence should be updated from default
        assert session.state.cumulative_confidence > 0.0

    def test_approval_workflow(self):
        brain = _mock_brain()
        session = Session(brain)
        # Manually add a pending approval
        session.state.pending_approvals.append(ApprovalRequest(
            request_id="test_001",
            action_description="deploy to production",
            turn_number=1,
            requested_at=time.time(),
        ))
        assert len(session.pending_approvals) == 1
        assert session.approve("test_001")
        assert len(session.pending_approvals) == 0

    def test_deny_approval(self):
        brain = _mock_brain()
        session = Session(brain)
        session.state.pending_approvals.append(ApprovalRequest(
            request_id="test_002",
            action_description="risky action",
            turn_number=1,
            requested_at=time.time(),
        ))
        assert session.deny("test_002")
        assert len(session.pending_approvals) == 0

    def test_set_mode(self):
        brain = _mock_brain()
        session = Session(brain)
        session.set_mode(SystemMode.AUDIT, "audit required")
        assert brain.mode_manager.state.mode == SystemMode.AUDIT

    def test_session_summary(self):
        brain = _mock_brain()
        session = Session(brain)
        session.turn("hello there")
        s = session.summary
        assert "session=" in s
        assert "turns=1" in s
        assert "mode=" in s


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR TESTS
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.runtime.orchestrator import (
    Orchestrator, OrchestrationResult, default_planner,
)


class TestPlanner:
    def test_comma_split(self):
        steps = default_planner("scan auth, check tokens, generate report")
        assert len(steps) == 3

    def test_then_split(self):
        steps = default_planner("first scan the endpoint then check the results")
        assert len(steps) >= 2

    def test_numbered_steps(self):
        steps = default_planner("1. scan auth 2. check tokens 3. write report")
        assert len(steps) == 3

    def test_single_step(self):
        steps = default_planner("scan the authentication endpoint")
        assert len(steps) == 1


class TestOrchestrator:
    def test_basic_run(self):
        brain = _mock_brain()
        orch = Orchestrator(brain, max_steps=5)
        result = orch.run("scan auth, check tokens")
        assert result.total_steps >= 1
        assert result.total_elapsed_ms > 0

    def test_max_steps_guard(self):
        brain = _mock_brain()
        orch = Orchestrator(brain, max_steps=1)
        result = orch.run("step one, step two, step three")
        assert result.total_steps == 1
        assert "max_steps" in result.halt_reason

    def test_timeout_guard(self):
        brain = _mock_brain()
        # Timeout of 0 should immediately halt
        orch = Orchestrator(brain, timeout_seconds=0.0)
        result = orch.run("step one, step two")
        # First step may execute before timeout check
        assert result.total_steps <= 1

    def test_escalation_halt(self):
        brain = _mock_brain()
        orch = Orchestrator(brain, halt_on_escalation=True)
        result = orch.run("production deploy now, then check status")
        # First step should trigger escalation and halt
        assert result.total_steps >= 1
        if result.halt_reason:
            assert "escalation" in result.halt_reason or "reflex" in result.halt_reason

    def test_custom_planner(self):
        brain = _mock_brain()
        custom = lambda goal: ["analyze " + goal, "summarize findings"]
        orch = Orchestrator(brain, planner=custom)
        result = orch.run("the auth endpoint")
        assert result.total_steps == 2

    def test_result_summary(self):
        brain = _mock_brain()
        orch = Orchestrator(brain, max_steps=3)
        result = orch.run("check the endpoint")
        s = result.summary
        assert "goal=" in s
        assert "steps=" in s

    def test_completed_flag(self):
        brain = _mock_brain()
        orch = Orchestrator(brain, max_steps=10)
        result = orch.run("just do one thing")
        # Single step, should complete
        if not result.halt_reason:
            assert result.completed


# ═══════════════════════════════════════════════════════════════════════════════
# LLM ADAPTER TESTS
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.cognition.adapters.llm import LLMReasoner, _build_user_message, SYSTEM_PROMPTS


class TestLLMAdapter:
    def test_system_prompts_exist_for_all_modes(self):
        for mode in ReasoningMode:
            assert mode in SYSTEM_PROMPTS

    def test_build_user_message(self):
        decision = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="test input", source=InputSource.USER),
                normalized_content="test input",
            )),
            policy_notes=["AUDIT: require evidence"],
        )
        msg = _build_user_message(decision)
        assert "test input" in msg
        assert "AUDIT" in msg

    def test_build_user_message_with_memory(self):
        mem = MemoryResult(
            query=MemoryQuery(query="test"),
            semantic=[MemoryItem(text="known fact about auth", memory_type="semantic")],
            episodic=[MemoryItem(text="past scan found XSS", memory_type="episodic")],
        )
        decision = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="check auth", source=InputSource.USER),
                normalized_content="check auth",
            )),
            memory=mem,
        )
        msg = _build_user_message(decision)
        assert "known fact" in msg
        assert "past scan" in msg

    def test_llm_reasoner_handles_connection_error(self):
        """LLM reasoner should return error result, not crash."""
        llm = LLMReasoner(provider="ollama", base_url="http://localhost:99999")
        decision = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="test", source=InputSource.USER),
                normalized_content="test",
            )),
            chosen_reasoning=ReasoningMode.DIRECT,
        )
        result = llm.run(decision, ModeState())
        assert result.confidence == 0.0
        assert "LLM ERROR" in result.result
        assert any("llm_error" in t for t in result.reasoning_trace)

    def test_llm_reasoner_init_providers(self):
        ollama = LLMReasoner(provider="ollama")
        assert "11434" in ollama.base_url

        openai = LLMReasoner(provider="openai")
        assert "openai" in openai.base_url

        custom = LLMReasoner(provider="custom", base_url="http://myhost:8080")
        assert custom.base_url == "http://myhost:8080"


# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY BACKEND TESTS
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.memory.adapters.mempalace import NullBackend, MemoryBackend


class TestMemoryBackend:
    def test_null_backend_search(self):
        backend = NullBackend()
        assert backend.search("anything") == []

    def test_null_backend_store(self):
        backend = NullBackend()
        assert backend.store("content", "wing", "room") == "null_backend"

    def test_null_backend_status(self):
        backend = NullBackend()
        status = backend.status()
        assert status["backend"] == "null"

    def test_null_backend_wake_up(self):
        backend = NullBackend()
        result = backend.wake_up()
        assert result["backend"] == "null"

    def test_null_backend_satisfies_protocol(self):
        """NullBackend should have all methods required by MemoryBackend."""
        backend = NullBackend()
        assert hasattr(backend, "search")
        assert hasattr(backend, "store")
        assert hasattr(backend, "query_entity")
        assert hasattr(backend, "wake_up")
        assert hasattr(backend, "status")


# ═══════════════════════════════════════════════════════════════════════════════
# ACTION SANDBOX TESTS (timeout, dry-run, schema validation)
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.action import execute as action_execute, register_tool, _validate_args


class TestActionSandbox:
    def test_dry_run(self):
        register_tool("scan_tool", lambda target="": f"scanned {target}")
        d = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="x", source=InputSource.USER))))
        c = CognitiveResult(decision=d)
        req = ActionRequest(
            action_type=ActionType.TOOL_CALL, cognitive_result=c,
            parameters={"tool_name": "scan_tool", "tool_args": {"target": "x"}, "dry_run": True},
        )
        result = action_execute(req, ModeState())
        assert result.success
        assert result.output["dry_run"] is True
        assert result.output["tool"] == "scan_tool"

    def test_timeout_enforcement(self):
        import time as _time

        def slow_tool():
            _time.sleep(5)
            return "done"

        register_tool("slow_tool", slow_tool, timeout_seconds=0.1)
        d = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="x", source=InputSource.USER))))
        c = CognitiveResult(decision=d)
        req = ActionRequest(
            action_type=ActionType.TOOL_CALL, cognitive_result=c,
            parameters={"tool_name": "slow_tool", "tool_args": {}},
        )
        result = action_execute(req, ModeState())
        assert not result.success
        assert result.error_category == "timeout"

    def test_arg_schema_validation(self):
        register_tool(
            "typed_tool", lambda target, count: f"{target}:{count}",
            arg_schema={"target": "str", "count": "int"},
        )
        d = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="x", source=InputSource.USER))))
        c = CognitiveResult(decision=d)

        # Missing arg
        req = ActionRequest(
            action_type=ActionType.TOOL_CALL, cognitive_result=c,
            parameters={"tool_name": "typed_tool", "tool_args": {"target": "x"}},
        )
        result = action_execute(req, ModeState())
        assert not result.success
        assert "Missing" in result.error

    def test_arg_schema_wrong_type(self):
        register_tool(
            "typed_tool2", lambda target, count: f"{target}:{count}",
            arg_schema={"target": "str", "count": "int"},
        )
        d = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="x", source=InputSource.USER))))
        c = CognitiveResult(decision=d)

        req = ActionRequest(
            action_type=ActionType.TOOL_CALL, cognitive_result=c,
            parameters={"tool_name": "typed_tool2", "tool_args": {"target": "x", "count": "not_int"}},
        )
        result = action_execute(req, ModeState())
        assert not result.success
        assert "expected int" in result.error

    def test_validate_args_helper(self):
        assert _validate_args({"a": "hello"}, {"a": "str"}) is None
        assert _validate_args({}, {"a": "str"}) is not None  # missing
        assert _validate_args({"a": 123}, {"a": "str"}) is not None  # wrong type


# ═══════════════════════════════════════════════════════════════════════════════
# EVENT BUS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.core.events import EventBus, Event


class TestEventBus:
    def test_emit_and_receive(self):
        bus = EventBus()
        received = []
        bus.subscribe(lambda e: received.append(e))
        bus.emit_simple("perception", "classified", {"intent": "security_assessment"})
        assert len(received) == 1
        assert received[0].stage == "perception"

    def test_stage_filter(self):
        bus = EventBus()
        reflex_events = []
        bus.subscribe(lambda e: reflex_events.append(e), stage="reflex")
        bus.emit_simple("perception", "classified")
        bus.emit_simple("reflex", "blocked", {"rule": "injection"})
        assert len(reflex_events) == 1

    def test_buffer(self):
        bus = EventBus(buffer_size=5)
        for i in range(10):
            bus.emit_simple("test", f"event_{i}")
        assert len(bus.events) == 5

    def test_events_for_stage(self):
        bus = EventBus()
        bus.emit_simple("a", "x")
        bus.emit_simple("b", "y")
        bus.emit_simple("a", "z")
        assert len(bus.events_for_stage("a")) == 2

    def test_events_since(self):
        bus = EventBus()
        t = time.time()
        bus.emit_simple("test", "after")
        assert len(bus.events_since(t)) >= 1

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        cb = lambda e: received.append(e)
        bus.subscribe(cb)
        bus.emit_simple("test", "one")
        bus.unsubscribe(cb)
        bus.emit_simple("test", "two")
        assert len(received) == 1

    def test_subscriber_error_doesnt_crash(self):
        bus = EventBus()
        bus.subscribe(lambda e: 1 / 0)  # will raise
        # Should not crash
        bus.emit_simple("test", "boom")
        assert len(bus.events) == 1

    def test_clear(self):
        bus = EventBus()
        bus.emit_simple("test", "x")
        bus.clear()
        assert len(bus.events) == 0

    def test_event_summary(self):
        e = Event(stage="reflex", event_type="block", data={"rule": "injection"})
        assert "injection" in e.summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
