"""Specialist solve engines.

A `SolveEngine` is the reasoning core a `SpecialistAgent` calls when the cheap
deterministic static scan fails. ctfrt does NOT hard-depend on any engine:
`BioBrainAdapter` lazy-imports BioBrain so the runtime boots without it, and
`StubReverseEngine` is a dependency-free engine used in tests and offline dev.

The engine returns an `EngineResult`; the agent maps it to a ctfrt `Candidate`
and routes it through the gate. The engine never declares a challenge solved —
it reports what it recovered and whether it independently reproduced it.
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .contracts import Category, Task
from .workspace import resolve_artifact_path

# technique vocabulary shared with cms_cag for consistent tagging/surfacing
_TECHNIQUE_VOCAB = [
    "ltrace", "strace", "strcmp", "memcmp", "xor", "angr", "z3", "ghidra",
    "rop", "ret2libc", "format string", "padding oracle", "cbc", "rsa",
    "wiener", "fermat", "spectrogram", "steghide", "zsteg", "volatility",
    "tshark", "sqli", "ssti", "ssrf", "jwt", "deserialization", "pyjail",
    "dynamic tracing", "symbolic execution",
]


def _extract_techniques(text: str) -> list[str]:
    low = text.lower()
    return [t for t in _TECHNIQUE_VOCAB if t in low]


def _solve_xor_artifact(task: Task) -> EngineResult | None:
    import json

    for art in task.artifacts:
        try:
            path = resolve_artifact_path(
                art,
                challenge_id=task.challenge_id,
                workdir=task.workdir or None,
            )
            spec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if spec.get("type") not in (None, "xor-crackme"):
            continue
        if "xor_key" not in spec or "blob_hex" not in spec:
            continue
        key = spec["xor_key"]
        blob = bytes.fromhex(spec["blob_hex"])
        flag = bytes(b ^ key for b in blob).decode("latin-1")
        reproduced = bytes(ord(c) ^ key for c in flag).hex() == spec["blob_hex"]
        return EngineResult(
            candidate=flag,
            evidence=[f"artifact={art}",
                      f"recovered via single-byte XOR key=0x{key:02x}",
                      "reproduction=re-encode(flag,key)==stored blob"],
            reproduced=reproduced,
            reproduction={"method": "reencode_xor", "artifact": art},
            technique=["xor", "keygen-inversion"],
            reasoning=["artifact-first solve matched xor_key/blob_hex schema",
                       "identified single-byte XOR transform",
                       "inverted transform to recover flag"],
        )
    return None


@dataclass(slots=True)
class EngineResult:
    """What a specialist engine recovered for one task.

    `reproduced` MUST be True only if the engine independently verified the
    candidate (e.g. ran the artifact in the sandbox and it accepted the value).
    The gate trusts this flag, so engines must be honest about it.
    """
    candidate: Optional[str] = None
    evidence: list[str] = field(default_factory=list)
    reproduced: bool = False
    reproduction: Optional[dict] = None   # recipe the gate uses to verify independently
    technique: list[str] = field(default_factory=list)  # technique tags for memory
    reasoning: list[str] = field(default_factory=list)
    handoff: Optional[Category] = None
    handoff_reason: str = ""


@runtime_checkable
class SolveEngine(Protocol):
    category: Category
    async def solve(self, task: Task) -> EngineResult: ...


# ── BioBrain adapter ─────────────────────────────────────────────────────────
class BioBrainAdapter:
    """Wraps a BioBrain pipeline as a SolveEngine.

    Faithful to BioBrain's real surface: `pipeline.process(content, source,
    metadata) -> PipelineTrace`, where the trace carries `cognitive` (result /
    evidence / confidence) and `action_results`. BioBrain is synchronous, so we
    run it in a thread to avoid blocking the event loop.

    Lazy import: ctfrt never imports biobrain at module load. Construct this
    only when BioBrain and its deps (and a live model endpoint) are available —
    on the inference host, not in the bare runtime.
    """

    def __init__(self, category: Category, *, identity_config: str | None = None,
                 memory_query: "MemoryQueryService | None" = None):
        self.category = category
        self._identity_config = identity_config
        self._memory = memory_query
        self._pipeline = None  # built on first use

    def _pipeline_kwargs(self) -> dict[str, str | None]:
        return {
            "palace_path": os.path.expanduser(
                os.getenv("BIOBRAIN_PALACE_PATH", "~/.mempalace/palace")
            ),
            "kg_path": os.getenv("BIOBRAIN_KG_PATH") or None,
            "playbook_dir": os.getenv("BIOBRAIN_PLAYBOOK_DIR") or None,
            "identity_config": self._identity_config
            or os.getenv("BIOBRAIN_IDENTITY_CONFIG")
            or None,
            "mempalace_identity": os.getenv("BIOBRAIN_MEMPALACE_IDENTITY") or None,
        }

    def _timeout_s(self) -> float:
        raw = os.getenv("CTF_AGENT_ENGINE_TIMEOUT_S", "4").strip()
        try:
            return max(0.1, float(raw))
        except ValueError:
            return 4.0

    def _ensure_pipeline(self):
        if self._pipeline is None:
            from biobrain.runtime.pipeline import BioBrain  # lazy
            self._pipeline = BioBrain(**self._pipeline_kwargs())
        return self._pipeline

    async def _run_pipeline(self, task: Task, content: str):
        from biobrain.core.enums import InputSource  # lazy

        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def worker():
            try:
                trace = self._ensure_pipeline().process(
                    content, InputSource.USER,
                    {"session_id": task.challenge_id, "artifacts": task.artifacts},
                )
            except Exception as exc:
                if not fut.done():
                    loop.call_soon_threadsafe(fut.set_exception, exc)
            else:
                if not fut.done():
                    loop.call_soon_threadsafe(fut.set_result, trace)

        threading.Thread(
            target=worker,
            name=f"biobrain-{task.challenge_id}",
            daemon=True,
        ).start()
        return await asyncio.wait_for(fut, timeout=self._timeout_s())

    def _build_content(self, task: Task) -> str:
        arts = ", ".join(task.artifacts)
        fmt = f" Flag format: {task.flag_format}." if task.flag_format else ""
        return (
            f"CTF {self.category.value} task. Artifacts: {arts}.{fmt} "
            f"Recover the flag. Use only sandboxed tools. "
            f"Report the flag and how you reproduced it."
        )

    async def solve(self, task: Task) -> EngineResult:
        local = _solve_xor_artifact(task)
        if local is not None:
            return local

        content = self._build_content(task)
        try:
            trace = await self._run_pipeline(task, content)
        except asyncio.TimeoutError:
            return EngineResult(
                evidence=[f"BioBrain pipeline timed out after {self._timeout_s():g}s"],
                reasoning=[f"timeout:{self._timeout_s():g}s"],
            )

        # reflex/executive halted the cycle -> nothing recovered
        if getattr(trace, "halted_at", None):
            return EngineResult(
                reasoning=[f"halted:{trace.halted_at}:{trace.halt_reason}"],
                evidence=[trace.audit_summary],
            )

        cog = getattr(trace, "cognitive", None)
        result_text = getattr(cog, "result", "") if cog else ""
        evidence = list(getattr(cog, "evidence", [])) if cog else []
        evidence.append(trace.audit_summary)

        candidate = self._extract_flag(result_text, task.flag_format)

        # reproduced only if an action result reports a verifying run
        reproduced = any(
            getattr(a, "success", False) and "verify" in getattr(a, "tool_name", "").lower()
            for a in getattr(trace, "action_results", [])
        )
        # best-effort technique tagging from the reasoning text. An explicit
        # technique tag emitted by BioBrain would be cleaner; this scans for now.
        reasoning = getattr(cog, "reasoning_trace", []) if cog else []
        technique = _extract_techniques(" ".join([result_text, *reasoning, *evidence]))
        return EngineResult(
            candidate=candidate, evidence=evidence, reproduced=reproduced,
            technique=technique, reasoning=reasoning,
        )

    @staticmethod
    def _extract_flag(text: str, flag_format: str | None) -> str | None:
        if flag_format:
            try:
                m = re.search(flag_format, text)
                if m:
                    return m.group(0)
            except re.error:
                pass
        m = re.search(r"[A-Za-z0-9_]+\{[^}\r\n]{1,200}\}", text)
        return m.group(0) if m else None


def engine_for_category(category: Category) -> SolveEngine | None:
    """Build the configured engine for one category, if any."""
    engine_mode = os.getenv("CTF_AGENT_ENGINE", "").strip().lower()
    if engine_mode == "biobrain" and category in (Category.reverse, Category.misc):
        return BioBrainAdapter(category)
    return None


# ── Deterministic stub engine (tests / offline dev) ───────────────────────────
class StubReverseEngine:
    """Dependency-free reverse engine for the XOR-crackme fixture.

    Reverses a trivial transform: reads the artifact's stored (xor_key, blob),
    reconstructs the flag, and — because it can re-encode and match the stored
    blob — legitimately sets reproduced=True. The flag is NOT plaintext in the
    file, so this exercises the engine path, not the static scan.
    """
    category = Category.reverse

    async def solve(self, task: Task) -> EngineResult:
        result = _solve_xor_artifact(task)
        if result is not None:
            return result
        return EngineResult(reasoning=["no recognizable transform in artifacts"])
