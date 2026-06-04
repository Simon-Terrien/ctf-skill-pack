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


_GENERIC_FLAG = re.compile(r"(?:flag|ctf|rootme|htb|hero|picoCTF)\{[^}\r\n]{1,200}\}", re.IGNORECASE)


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
        await self.bus.publish(Topics.TRACES, TraceEvent(
            challenge_id=challenge_id, category=self.category, kind=kind, payload=payload))

    async def _run_in_sandbox(self, req: SandboxRequest) -> SandboxResult:
        await self.bus.publish(Topics.SANDBOX_REQ, req, key=req.challenge_id)
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
            p = Path(artifact)
            try:
                data = p.read_bytes()
            except OSError:
                continue
            text = data.decode("latin-1", errors="ignore")
            for pat in patterns:
                m = pat.search(text)
                if m:
                    return m.group(0), str(p)
        return None

    async def handle(self, task: Task) -> None:
        await self._trace(task.challenge_id, "task_started", {"category": self.category.value})

        static = self._find_static_flag(task)
        if static:
            flag, artifact = static
            await self.bus.publish(Topics.CANDIDATES, Candidate(
                challenge_id=task.challenge_id,
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
            await self._trace(task.challenge_id, "candidate_emitted", {"source": "static-artifact-scan"})
            return

        # static scan failed -> hand off to the reasoning engine, if wired
        if self.engine is None:
            await self._trace(task.challenge_id, "needs_engine", {
                "reason": "no static flag and no SolveEngine attached",
            })
            return

        self.researcher.bind_trace(lambda kind, payload: self._trace(task.challenge_id, kind, payload))
        try:
            await self.researcher.lookup(
                question=f"{self.category.value} challenge {task.challenge_id}",
                tokens=[Path(a).name for a in task.artifacts[:3]] or [task.challenge_id],
            )
        except Exception:
            # Tool audit already captured the failure; continue with the solve path.
            pass

        await self._trace(task.challenge_id, "engine_dispatch", {"engine": type(self.engine).__name__})
        try:
            result = await self.engine.solve(task)
        except Exception as exc:
            await self._trace(task.challenge_id, "engine_error", {
                "engine": type(self.engine).__name__,
                "error": repr(exc),
            })
            return

        if result.handoff is not None:
            await self.bus.publish(Topics.HANDOFFS, Handoff(
                challenge_id=task.challenge_id, from_category=self.category,
                target=result.handoff, reason=result.handoff_reason or "engine reclassified",
            ), key=task.challenge_id)
            await self._trace(task.challenge_id, "handoff", {"target": result.handoff.value})
            return

        if not result.candidate:
            await self._trace(task.challenge_id, "engine_no_candidate",
                              {"reasoning": result.reasoning})
            return

        # Map engine result -> ctfrt Candidate. The engine's honest `reproduced`
        # flag sets the proof tier; the gate still validates independently.
        await self.bus.publish(Topics.CANDIDATES, Candidate(
            challenge_id=task.challenge_id,
            candidate=result.candidate,
            source=f"{self.category.value}:{type(self.engine).__name__}",
            flag_format=task.flag_format,
            validation_level="reproduced" if result.reproduced else "observed",
            local_validation="passed" if result.reproduced else "not_attempted",
            oracle_validation="not_available",
            evidence=result.evidence,
            reproduction=result.reproduction,
            technique=result.technique,
        ), key=task.challenge_id)
        await self._trace(task.challenge_id, "candidate_emitted",
                          {"source": type(self.engine).__name__, "reproduced": result.reproduced})

    async def run(self) -> None:
        group = f"specialist-{self.category.value}"
        async for raw in self.bus.subscribe(Topics.tasks_for(self.category), group=group):
            task = Task.model_validate(raw)
            if task.category != self.category:
                await self._trace(task.challenge_id, "ignored_wrong_category", {"task_category": task.category.value})
                continue
            try:
                await self.handle(task)
            except Exception as e:
                await self._trace(task.challenge_id, "error", {"error": repr(e)})
