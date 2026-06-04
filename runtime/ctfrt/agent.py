"""Base specialist agent.

One worker per category. Consumes ctf.tasks, loads its SKILL.md as the operating
SOP, runs cheap deterministic checks first, and leaves a clear seam for the
future model/tool loop.
"""
from __future__ import annotations

import re
from pathlib import Path

from .bus import Bus
from .config import Topics
from .contracts import Candidate, Category, Handoff, Hypothesis, SandboxRequest, SandboxResult, Task, TraceEvent
from .llm import LLM, load_skill
from .memory import MemoryProtocol
from .tools import Researcher
from .engines import SolveEngine
from .workspace import resolve_artifact_path
from .log import get_logger, kv


_GENERIC_FLAG = re.compile(r"(?:flag|ctf|rootme|htb|hero|picoCTF)\{[^}\r\n]{1,200}\}", re.IGNORECASE)
_MAX_TOOL_STEPS = 4   # max engine iterations before declaring no candidate
_MAX_BARREN = 2       # consecutive barren steps before early stop
log = get_logger(__name__)


class SpecialistAgent:
    MAX_STEPS = 40

    def __init__(self, category: Category, bus: Bus, mem: MemoryProtocol,
                 llm: LLM | None, researcher: Researcher,
                 engine: "SolveEngine | None" = None):
        self.category = category
        self.bus = bus
        self.mem = mem
        self.llm = llm
        self.researcher = researcher
        self.engine = engine        # reasoning core; None -> static scan only
        self.sop = load_skill(category.value)

    async def _trace(self, challenge_id: str, kind: str, payload: dict) -> None:
        log.debug("publish topic", extra=kv(
            topic=Topics.TRACES, challenge_id=challenge_id, kind=kind))
        await self.bus.publish(Topics.TRACES, TraceEvent(
            challenge_id=challenge_id, category=self.category, kind=kind, payload=payload))

    async def _run_in_sandbox(self, req: SandboxRequest) -> SandboxResult:
        log.debug("publish topic", extra=kv(
            topic=Topics.SANDBOX_REQ, challenge_id=req.challenge_id, request_id=req.id))
        await self.bus.publish(Topics.SANDBOX_REQ, req, key=req.challenge_id)
        log.debug("subscribe topic", extra=kv(
            topic=Topics.SANDBOX_RES, group=f"agent-{self.category.value}", challenge_id=req.challenge_id))
        async for raw in self.bus.subscribe(Topics.SANDBOX_RES, group=f"agent-{self.category.value}"):
            res = SandboxResult.model_validate(raw)
            if res.request_id == req.id:
                return res

    def _find_static_flag(self, task: Task) -> tuple[str, str] | None:
        patterns: list[re.Pattern[str]] = []
        if task.flag_format:
            try:
                patterns.append(re.compile(task.flag_format))
            except re.error:
                pass
        patterns.append(_GENERIC_FLAG)

        for artifact in task.artifacts:
            try:
                p = resolve_artifact_path(
                    artifact,
                    challenge_id=task.challenge_id,
                    workdir=task.workdir or None,
                )
                data = p.read_bytes()
            except (OSError, ValueError):
                continue
            text = data.decode("latin-1", errors="ignore")
            for pat in patterns:
                m = pat.search(text)
                if m:
                    return m.group(0), artifact
        return None

    async def handle(self, task: Task) -> None:
        log.info("task handling started", extra=kv(
            challenge_id=task.challenge_id, category=self.category.value, artifact_count=len(task.artifacts)))
        await self._trace(task.challenge_id, "task_started", {"category": self.category.value})

        static = self._find_static_flag(task)
        if static:
            flag, artifact = static
            await self.bus.publish(Topics.CANDIDATES, Candidate(
                challenge_id=task.challenge_id,
                workdir=task.workdir,
                candidate=flag,
                source=f"{self.category.value}:static-artifact-scan",
                flag_format=task.flag_format,
                technique=["static-artifact-scan"],
                validation_level="reproduced",
                local_validation="passed",
                oracle_validation="not_available",
                evidence=[
                    f"artifact={artifact}",
                    "reproduction=read artifact bytes -> latin-1 decode -> regex search",
                ],
            ), key=task.challenge_id)
            log.debug("publish topic", extra=kv(
                topic=Topics.CANDIDATES, challenge_id=task.challenge_id, source="static-artifact-scan"))
            await self._trace(task.challenge_id, "candidate_emitted", {"source": "static-artifact-scan"})
            return

        # static scan failed -> hand off to the reasoning engine, if wired
        if self.engine is None:
            log.info("task requires engine", extra=kv(
                challenge_id=task.challenge_id, category=self.category.value))
            await self._trace(task.challenge_id, "needs_engine", {
                "reason": "no static flag and no SolveEngine attached",
            })
            return

        self.researcher.bind_trace(lambda kind, payload: self._trace(task.challenge_id, kind, payload))
        bind_trace = getattr(self.engine, "bind_trace", None)
        if callable(bind_trace):
            bind_trace(lambda kind, payload: self._trace(task.challenge_id, kind, payload))
        try:
            await self.researcher.lookup(
                question=f"{self.category.value} challenge {task.challenge_id}",
                tokens=[Path(a).name for a in task.artifacts[:3]] or [task.challenge_id],
            )
        except Exception:
            # Tool audit already captured the failure; continue with the solve path.
            pass

        # Bounded step loop: up to _MAX_TOOL_STEPS engine calls.
        # Each step emits a Hypothesis to the memory ledger so the orchestrator
        # (and future memory consolidation) can track reasoning progress.
        # Two consecutive barren steps trigger early stop.
        barren = 0
        hypothesis_count = 0
        engine_name = type(self.engine).__name__

        for step in range(1, _MAX_TOOL_STEPS + 1):
            await self._trace(task.challenge_id, "engine_dispatch",
                              {"engine": engine_name, "step": step})
            try:
                result = await self.engine.solve(task)
            except Exception as exc:
                await self._trace(task.challenge_id, "engine_error", {
                    "engine": engine_name,
                    "step": step,
                    "error": repr(exc),
                })
                return

            if result.handoff is not None:
                log.debug("publish topic", extra=kv(
                    topic=Topics.HANDOFFS, challenge_id=task.challenge_id,
                    target=result.handoff.value))
                await self.bus.publish(Topics.HANDOFFS, Handoff(
                    challenge_id=task.challenge_id, from_category=self.category,
                    target=result.handoff, reason=result.handoff_reason or "engine reclassified",
                ), key=task.challenge_id)
                await self._trace(task.challenge_id, "handoff",
                                  {"target": result.handoff.value, "step": step})
                return

            # Emit a hypothesis for each step so memory can track progress.
            if result.evidence or result.reasoning:
                confidence_val = "medium" if result.candidate else "low"
                h = Hypothesis(
                    challenge_id=task.challenge_id,
                    category=self.category,
                    claim=result.candidate or "; ".join(result.reasoning[:2]) or "no candidate",
                    confidence=confidence_val,
                    evidence=result.evidence,
                    next_test="" if result.candidate else "retry or escalate",
                    exit_condition="candidate confirmed" if result.candidate else "max_steps",
                    result="confirmed" if result.candidate else "open",
                    iterations=step,
                )
                try:
                    await self.mem.upsert_hypothesis(h)
                except Exception:
                    pass
                log.debug("publish topic", extra=kv(
                    topic=Topics.HYPOTHESES, challenge_id=task.challenge_id))
                await self.bus.publish(Topics.HYPOTHESES, h, key=task.challenge_id)
                hypothesis_count += 1

            if result.candidate:
                # Map engine result -> ctfrt Candidate. The engine's honest
                # `reproduced` flag sets the proof tier; gate validates independently.
                await self.bus.publish(Topics.CANDIDATES, Candidate(
                    challenge_id=task.challenge_id,
                    workdir=task.workdir,
                    candidate=result.candidate,
                    source=f"{self.category.value}:{engine_name}",
                    flag_format=task.flag_format,
                    validation_level="reproduced" if result.reproduced else "observed",
                    local_validation="passed" if result.reproduced else "not_attempted",
                    oracle_validation="not_available",
                    evidence=result.evidence,
                    reproduction=result.reproduction,
                    technique=result.technique,
                ), key=task.challenge_id)
                log.debug("publish topic", extra=kv(
                    topic=Topics.CANDIDATES, challenge_id=task.challenge_id,
                    source=engine_name))
                await self._trace(task.challenge_id, "candidate_emitted",
                                  {"source": engine_name, "reproduced": result.reproduced,
                                   "step": step})
                return

            # Barren step — no candidate, no handoff.
            barren += 1
            if barren >= _MAX_BARREN:
                log.info("barren stop", extra=kv(
                    challenge_id=task.challenge_id, barren=barren, step=step))
                break

        await self._trace(task.challenge_id, "engine_no_candidate", {
            "reasoning": "; ".join(result.reasoning) if result.reasoning else "",
            "evidence": " | ".join(result.evidence) if result.evidence else "",
            "technique": result.technique,
            "hypothesis_count": hypothesis_count,
            "steps": step,
        })

    async def run(self) -> None:
        group = f"specialist-{self.category.value}"
        log.info("specialist started", extra=kv(category=self.category.value, group=group))
        log.debug("subscribe topic", extra=kv(topic=Topics.tasks_for(self.category), group=group))
        async for raw in self.bus.subscribe(Topics.tasks_for(self.category), group=group):
            task = Task.model_validate(raw)
            if task.category != self.category:
                await self._trace(task.challenge_id, "ignored_wrong_category", {"task_category": task.category.value})
                continue
            try:
                await self.handle(task)
            except Exception as e:
                await self._trace(task.challenge_id, "error", {"error": repr(e)})
