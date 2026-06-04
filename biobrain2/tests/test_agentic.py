"""
Tests for agentic runtime: model router, specialized agents, benchmark harness.
"""

import pytest
from unittest.mock import patch, MagicMock

from biobrain.core.enums import (
    InputSource, SystemMode, ReasoningMode, OperationClass,
)
from biobrain.core.signals import (
    RawInput, PerceivedInput, SalienceScore, ExecutiveDecision,
    CognitiveResult, ModeState, MemoryResult, MemoryQuery,
)


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.cognition.router import (
    ModelRouter, ModelSpec, DEFAULT_MODELS, REASONING_TO_ROLE,
)


class TestModelSpec:
    def test_display(self):
        spec = ModelSpec(model="qwen3-coder", provider="ollama")
        assert spec.display == "ollama/qwen3-coder"


class TestModelRouter:
    def test_from_defaults(self):
        router = ModelRouter.from_defaults()
        assert "planner" in router.available_roles
        assert "coder" in router.available_roles
        assert "critic" in router.available_roles

    def test_from_config(self):
        config = {
            "planner": {"model": "qwen3.6"},
            "coder": {"model": "qwen3-coder"},
        }
        router = ModelRouter.from_config(config)
        assert router.get_model("planner").model == "qwen3.6"
        assert router.get_model("coder").model == "qwen3-coder"

    def test_fallback_model(self):
        router = ModelRouter.from_config({"fast": {"model": "tiny"}})
        # Unknown role falls back to "fast"
        spec = router.get_model("unknown_role")
        assert spec.model == "tiny"

    def test_model_summary(self):
        router = ModelRouter.from_defaults()
        summary = router.model_summary()
        assert len(summary) >= 5
        assert all("/" in v for v in summary.values())

    def test_reasoning_to_role_mapping(self):
        assert REASONING_TO_ROLE[ReasoningMode.PLANNING] == "planner"
        assert REASONING_TO_ROLE[ReasoningMode.CRITIC] == "critic"
        assert REASONING_TO_ROLE[ReasoningMode.CHECKLIST] == "security"

    def test_route_handles_llm_error(self):
        """Router should return error result, not crash."""
        router = ModelRouter.from_config({
            "coder": {"model": "nonexistent", "base_url": "http://localhost:99999"},
        })
        decision = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="write code", source=InputSource.USER),
                normalized_content="write code",
            )),
        )
        result = router.route("coder", decision, ModeState())
        assert result.cognitive_result.confidence == 0.0
        # Error can come from LLMReasoner ("LLM ERROR") or router ("ROUTER ERROR")
        assert "ERROR" in result.cognitive_result.result

    def test_auto_route_maps_reasoning(self):
        router = ModelRouter.from_defaults()
        decision = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="plan the deployment", source=InputSource.USER),
                normalized_content="plan the deployment",
            )),
            chosen_reasoning=ReasoningMode.PLANNING,
        )
        # Mock the _call_model to avoid actual LLM call
        router._call_model = MagicMock(return_value=CognitiveResult(
            decision=decision, result="mocked", confidence=0.8,
        ))
        result = router.auto_route(decision, ModeState())
        assert result.role == "planner"

    def test_auto_route_incident_mode_uses_fast(self):
        router = ModelRouter.from_defaults()
        decision = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="check status", source=InputSource.USER),
                normalized_content="check status",
            )),
            chosen_reasoning=ReasoningMode.PLANNING,
        )
        router._call_model = MagicMock(return_value=CognitiveResult(
            decision=decision, result="fast", confidence=0.7,
        ))
        result = router.auto_route(decision, ModeState(mode=SystemMode.INCIDENT))
        assert result.role == "fast"

    def test_call_history_recorded(self):
        router = ModelRouter.from_config({
            "fast": {"model": "test", "base_url": "http://localhost:99999"},
        })
        decision = ExecutiveDecision(
            salience=SalienceScore(perceived=PerceivedInput(
                raw=RawInput(content="test", source=InputSource.USER),
                normalized_content="test",
            )),
        )
        router.route("fast", decision, ModeState())
        assert len(router.call_history) == 1
        assert router.call_history[0]["role"] == "fast"


# ═══════════════════════════════════════════════════════════════════════════════
# AGENTS
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.agents import (
    PlannerAgent, CoderAgent, CriticAgent, SecurityAgent,
    create_agent, AGENT_REGISTRY, AgentResult,
)


class TestAgentRegistry:
    def test_available_agents(self):
        assert "planner" in AGENT_REGISTRY
        assert "coder" in AGENT_REGISTRY
        assert "critic" in AGENT_REGISTRY
        assert "security" in AGENT_REGISTRY

    def test_create_agent(self):
        router = ModelRouter.from_defaults()
        agent = create_agent("planner", router)
        assert isinstance(agent, PlannerAgent)

    def test_create_unknown_agent(self):
        router = ModelRouter.from_defaults()
        with pytest.raises(ValueError, match="Unknown agent"):
            create_agent("nonexistent", router)


class TestPlannerAgent:
    def test_plan_returns_result(self):
        router = ModelRouter.from_defaults()
        agent = PlannerAgent(router)
        # Mock the route to avoid LLM call
        agent._route = MagicMock(return_value=MagicMock(
            cognitive_result=CognitiveResult(
                decision=MagicMock(),
                result="1. First step\n2. Second step\n3. Third step",
                confidence=0.8,
            ),
            model_spec=ModelSpec(model="test"),
            latency_ms=100,
        ))
        result = agent.plan("build an auth system")
        assert result.agent_name == "planner"
        assert result.structured_data["steps_count"] == 3

    def test_plan_parses_steps(self):
        router = ModelRouter.from_defaults()
        agent = PlannerAgent(router)
        agent._route = MagicMock(return_value=MagicMock(
            cognitive_result=CognitiveResult(
                decision=MagicMock(),
                result="1. Design the schema\n2. Implement endpoints\n3. Write tests\n4. Deploy",
                confidence=0.7,
            ),
            model_spec=ModelSpec(model="test"),
            latency_ms=50,
        ))
        result = agent.plan("build API")
        steps = result.structured_data["steps"]
        assert len(steps) == 4
        assert "Design the schema" in steps[0]


class TestCoderAgent:
    def test_generate(self):
        router = ModelRouter.from_defaults()
        agent = CoderAgent(router)
        agent._route = MagicMock(return_value=MagicMock(
            cognitive_result=CognitiveResult(
                decision=MagicMock(),
                result="def hello(): return 'world'",
                confidence=0.9,
            ),
            model_spec=ModelSpec(model="qwen3-coder"),
            latency_ms=200,
        ))
        result = agent.generate("hello world function")
        assert result.agent_name == "coder"
        assert result.model_used == "qwen3-coder"

    def test_review(self):
        router = ModelRouter.from_defaults()
        agent = CoderAgent(router)
        agent._route = MagicMock(return_value=MagicMock(
            cognitive_result=CognitiveResult(
                decision=MagicMock(), result="No issues found", confidence=0.8,
            ),
            model_spec=ModelSpec(model="test"),
            latency_ms=100,
        ))
        result = agent.review("print('hello')")
        assert "quality" in result.structured_data.get("review_type", "")


class TestSecurityAgent:
    def test_review_code(self):
        router = ModelRouter.from_defaults()
        agent = SecurityAgent(router)
        agent._route = MagicMock(return_value=MagicMock(
            cognitive_result=CognitiveResult(
                decision=MagicMock(),
                result="XSS vulnerability found in line 3",
                confidence=0.85,
            ),
            model_spec=ModelSpec(model="qwen3.6"),
            latency_ms=300,
        ))
        result = agent.review_code("return f'<h1>{user_input}</h1>'")
        assert result.agent_name == "security_reviewer"
        assert result.structured_data["review_type"] == "security"


# ═══════════════════════════════════════════════════════════════════════════════
# BENCHMARK HARNESS
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.agents.benchmark import (
    BenchmarkHarness, BenchmarkTask, BenchmarkResult, SuiteResult,
    CODING_SUITE, REASONING_SUITE, SECURITY_SUITE, SUITES,
)


class TestBenchmarkSuites:
    def test_suites_exist(self):
        assert "coding" in SUITES
        assert "reasoning" in SUITES
        assert "security" in SUITES

    def test_coding_suite_has_tasks(self):
        assert len(CODING_SUITE) >= 3

    def test_tasks_have_keywords(self):
        for task in CODING_SUITE:
            assert len(task.expected_keywords) >= 1


class TestBenchmarkResult:
    def test_keyword_score(self):
        r = BenchmarkResult(
            task_name="test", model="m",
            keywords_matched=3, keywords_total=5,
        )
        assert abs(r.keyword_score - 0.6) < 0.01

    def test_keyword_score_no_keywords(self):
        r = BenchmarkResult(task_name="test", model="m")
        assert r.keyword_score == 1.0

    def test_to_dict(self):
        r = BenchmarkResult(
            task_name="test", model="qwen3", latency_ms=100,
            confidence=0.8, keywords_matched=2, keywords_total=3,
        )
        d = r.to_dict()
        assert d["model"] == "qwen3"
        assert d["latency_ms"] == 100


class TestSuiteResult:
    def test_model_summary(self):
        sr = SuiteResult(
            suite_name="test",
            models_tested=["a", "b"],
            results=[
                BenchmarkResult(task_name="t1", model="a", latency_ms=100, confidence=0.8),
                BenchmarkResult(task_name="t1", model="b", latency_ms=200, confidence=0.6),
            ],
        )
        summary = sr.model_summary()
        assert len(summary) == 2
        assert summary[0]["model"] == "a"  # higher confidence first


class TestBenchmarkHarness:
    def test_run_suite_with_connection_errors(self):
        """Should handle LLM connection errors gracefully."""
        router = ModelRouter.from_config({
            "fast": {"model": "nonexistent", "base_url": "http://localhost:99999"},
        })
        harness = BenchmarkHarness(router)

        # Use minimal custom tasks
        tasks = [BenchmarkTask(
            name="test_task", suite="test", prompt="hello",
            expected_keywords=["world"], max_time_ms=5000,
        )]
        result = harness.run_suite("test", models=["nonexistent"], custom_tasks=tasks)
        assert result.suite_name == "test"
        assert len(result.results) == 1
        # Error shows up either as .error or as zero confidence with ERROR in output
        r = result.results[0]
        assert r.error is not None or r.confidence == 0.0

    def test_print_results(self):
        sr = SuiteResult(
            suite_name="test",
            models_tested=["a"],
            results=[
                BenchmarkResult(task_name="t1", model="a", latency_ms=100, confidence=0.8),
            ],
        )
        output = BenchmarkHarness.print_results(sr)
        assert "Benchmark: test" in output


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG MULTI-MODEL
# ═══════════════════════════════════════════════════════════════════════════════

from biobrain.config import load_config, Settings
import tempfile
import os


class TestConfigMultiModel:
    def test_router_config_from_models(self):
        cfg = Settings(models={
            "planner": {"model": "qwen3.6"},
            "coder": {"model": "qwen3-coder"},
        })
        rc = cfg.router_config
        assert rc["planner"]["model"] == "qwen3.6"
        assert rc["coder"]["model"] == "qwen3-coder"

    def test_router_config_fallback(self):
        cfg = Settings(llm_model="mistral-nemo")
        rc = cfg.router_config
        assert rc["planner"]["model"] == "mistral-nemo"
        assert rc["coder"]["model"] == "mistral-nemo"

    def test_load_multimodel_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
models:
  planner:
    model: "qwen3.6"
  coder:
    model: "qwen3-coder"
""")
            path = f.name
        try:
            cfg = load_config(path)
            assert cfg.models is not None
            assert cfg.models["planner"]["model"] == "qwen3.6"
        finally:
            os.unlink(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
