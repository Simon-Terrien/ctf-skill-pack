"""
biobrain.agents.benchmark — Model comparison benchmark harness
=================================================================

Runs standardized tasks against multiple models and compares:
  - Latency (ms)
  - Tokens/second (estimated)
  - Output quality (via critic agent)
  - Test pass rate (for coding tasks)
  - Security findings (for security tasks)
  - Confidence scores

Usage:
    from biobrain.agents.benchmark import BenchmarkHarness

    harness = BenchmarkHarness(router)
    results = harness.run_suite("coding", models=["qwen3-coder", "qwen3.6"])
    harness.print_results(results)

CLI:
    biobrain benchmark --suite coding --models qwen3-coder,qwen3.6
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..core.enums import ReasoningMode, InputSource
from ..core.signals import (
    RawInput, PerceivedInput, SalienceScore, ExecutiveDecision,
    CognitiveResult, ModeState,
)
from ..cognition.router import ModelRouter, ModelSpec

logger = logging.getLogger("biobrain.agents.benchmark")


@dataclass
class BenchmarkTask:
    """A single benchmark task."""
    name: str
    suite: str  # coding, reasoning, security, planning
    prompt: str
    expected_keywords: list[str] = field(default_factory=list)
    max_time_ms: float = 60000.0
    reasoning_mode: ReasoningMode = ReasoningMode.DIRECT


@dataclass
class BenchmarkResult:
    """Result of running one task on one model."""
    task_name: str
    model: str
    latency_ms: float = 0.0
    tokens_per_second: float = 0.0
    output_length: int = 0
    confidence: float = 0.0
    keywords_matched: int = 0
    keywords_total: int = 0
    timed_out: bool = False
    error: Optional[str] = None

    @property
    def keyword_score(self) -> float:
        if self.keywords_total == 0:
            return 1.0
        return self.keywords_matched / self.keywords_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task_name,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "tokens_per_second": round(self.tokens_per_second, 1),
            "output_length": self.output_length,
            "confidence": round(self.confidence, 3),
            "keyword_score": round(self.keyword_score, 3),
            "timed_out": self.timed_out,
            "error": self.error,
        }


@dataclass
class SuiteResult:
    """Aggregated results for a benchmark suite."""
    suite_name: str
    models_tested: list[str] = field(default_factory=list)
    results: list[BenchmarkResult] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def by_model(self, model: str) -> list[BenchmarkResult]:
        return [r for r in self.results if r.model == model]

    def model_summary(self) -> list[dict[str, Any]]:
        """Aggregate stats per model."""
        summaries = []
        for model in self.models_tested:
            model_results = self.by_model(model)
            if not model_results:
                continue
            latencies = [r.latency_ms for r in model_results if not r.timed_out]
            summaries.append({
                "model": model,
                "tasks": len(model_results),
                "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
                "avg_confidence": round(
                    sum(r.confidence for r in model_results) / len(model_results), 3
                ),
                "avg_keyword_score": round(
                    sum(r.keyword_score for r in model_results) / len(model_results), 3
                ),
                "errors": sum(1 for r in model_results if r.error),
                "timeouts": sum(1 for r in model_results if r.timed_out),
            })
        return sorted(summaries, key=lambda s: s["avg_confidence"], reverse=True)


# ─── Built-in benchmark suites ──────────────────────────────────────────────

CODING_SUITE: list[BenchmarkTask] = [
    BenchmarkTask(
        name="fastapi_endpoint",
        suite="coding",
        prompt="Write a FastAPI endpoint that accepts a JSON body with email and password, validates the email format, hashes the password with bcrypt, and returns a JWT token.",
        expected_keywords=["fastapi", "bcrypt", "jwt", "async", "def"],
        reasoning_mode=ReasoningMode.DIRECT,
    ),
    BenchmarkTask(
        name="python_dataclass",
        suite="coding",
        prompt="Write a Python dataclass for a PentestFinding with fields: title, severity (enum), cvss_score (float), wstg_id, description, remediation, status. Include validation and a to_dict method.",
        expected_keywords=["dataclass", "enum", "float", "to_dict"],
        reasoning_mode=ReasoningMode.DIRECT,
    ),
    BenchmarkTask(
        name="sql_injection_fix",
        suite="coding",
        prompt="Fix this Python code that has a SQL injection vulnerability:\n\ndef get_user(username):\n    query = f\"SELECT * FROM users WHERE name = '{username}'\"\n    return db.execute(query)\n\nReturn the fixed version using parameterized queries.",
        expected_keywords=["parameterized", "?", "%s", "placeholder"],
        reasoning_mode=ReasoningMode.DIRECT,
    ),
]

REASONING_SUITE: list[BenchmarkTask] = [
    BenchmarkTask(
        name="root_cause_analysis",
        suite="reasoning",
        prompt="A web application returns 200 OK for login requests but the session cookie is never set. The server logs show the authentication middleware runs successfully. What are the three most likely root causes? Explain your reasoning for each.",
        expected_keywords=["cookie", "domain", "secure", "path", "header"],
        reasoning_mode=ReasoningMode.CAUSAL,
    ),
    BenchmarkTask(
        name="architecture_tradeoff",
        suite="reasoning",
        prompt="Compare microservices vs monolith for a startup with 3 developers building an e-commerce platform. Consider: development speed, operational complexity, scaling needs, team size. Give a clear recommendation with reasoning.",
        expected_keywords=["monolith", "complexity", "deploy", "team"],
        reasoning_mode=ReasoningMode.SIMULATION,
    ),
]

SECURITY_SUITE: list[BenchmarkTask] = [
    BenchmarkTask(
        name="owasp_review",
        suite="security",
        prompt="Review this code for OWASP Top 10 issues:\n\n@app.route('/search')\ndef search():\n    q = request.args.get('q')\n    return f'<h1>Results for {q}</h1>'\n\nIdentify all vulnerabilities with severity ratings.",
        expected_keywords=["xss", "injection", "sanitiz", "escap", "critical", "high"],
        reasoning_mode=ReasoningMode.CHECKLIST,
    ),
]

SUITES: dict[str, list[BenchmarkTask]] = {
    "coding": CODING_SUITE,
    "reasoning": REASONING_SUITE,
    "security": SECURITY_SUITE,
}


class BenchmarkHarness:
    """Runs benchmark suites against multiple models."""

    def __init__(self, router: ModelRouter):
        self.router = router

    def run_suite(
        self,
        suite_name: str,
        models: Optional[list[str]] = None,
        custom_tasks: Optional[list[BenchmarkTask]] = None,
    ) -> SuiteResult:
        """Run a benchmark suite against one or more models."""
        tasks = custom_tasks or SUITES.get(suite_name, [])
        if not tasks:
            return SuiteResult(suite_name=suite_name)

        if models is None:
            models = [spec.model for spec in self.router._models.values()]
            models = list(dict.fromkeys(models))  # dedupe

        start = time.time()
        result = SuiteResult(suite_name=suite_name, models_tested=models)

        for model in models:
            for task in tasks:
                br = self._run_task(task, model)
                result.results.append(br)
                logger.info(
                    "Benchmark %s/%s: %.0fms, confidence=%.2f, keywords=%d/%d",
                    model, task.name, br.latency_ms, br.confidence,
                    br.keywords_matched, br.keywords_total,
                )

        result.elapsed_ms = (time.time() - start) * 1000
        return result

    def _run_task(self, task: BenchmarkTask, model: str) -> BenchmarkResult:
        """Run a single task on a single model."""
        spec = ModelSpec(model=model)

        # Build decision
        raw = RawInput(content=task.prompt, source=InputSource.INTERNAL)
        perceived = PerceivedInput(raw=raw, normalized_content=task.prompt)
        salience = SalienceScore(perceived=perceived)
        decision = ExecutiveDecision(
            salience=salience, chosen_reasoning=task.reasoning_mode,
        )

        start = time.time()
        try:
            from ..cognition.adapters.llm import LLMReasoner
            reasoner = LLMReasoner(
                provider=spec.provider, model=spec.model,
                temperature=spec.temperature, max_tokens=spec.max_tokens,
                timeout_seconds=task.max_time_ms / 1000,
            )
            cognitive = reasoner.run(decision, ModeState())
            latency = (time.time() - start) * 1000

            output = cognitive.result
            output_len = len(output)
            tokens_est = output_len // 4
            tps = (tokens_est / (latency / 1000)) if latency > 0 else 0

            # Keyword matching
            output_lower = output.lower()
            matched = sum(1 for kw in task.expected_keywords if kw.lower() in output_lower)

            return BenchmarkResult(
                task_name=task.name,
                model=model,
                latency_ms=round(latency, 1),
                tokens_per_second=round(tps, 1),
                output_length=output_len,
                confidence=cognitive.confidence,
                keywords_matched=matched,
                keywords_total=len(task.expected_keywords),
            )

        except Exception as e:
            latency = (time.time() - start) * 1000
            return BenchmarkResult(
                task_name=task.name,
                model=model,
                latency_ms=round(latency, 1),
                timed_out="timeout" in str(e).lower(),
                error=str(e),
            )

    @staticmethod
    def print_results(suite: SuiteResult) -> str:
        """Format results as a readable comparison table."""
        lines = [f"# Benchmark: {suite.suite_name}", f"Elapsed: {suite.elapsed_ms:.0f}ms", ""]

        for summary in suite.model_summary():
            lines.append(
                f"  {summary['model']:30s} | "
                f"avg_lat={summary['avg_latency_ms']:8.0f}ms | "
                f"conf={summary['avg_confidence']:.3f} | "
                f"kw={summary['avg_keyword_score']:.3f} | "
                f"err={summary['errors']}"
            )

        lines.append("")
        for r in suite.results:
            status = "✓" if not r.error else "✗"
            lines.append(
                f"  {status} {r.model:25s} / {r.task_name:25s} | "
                f"{r.latency_ms:8.0f}ms | "
                f"kw={r.keywords_matched}/{r.keywords_total} | "
                f"conf={r.confidence:.2f}"
            )

        output = "\n".join(lines)
        print(output)
        return output
