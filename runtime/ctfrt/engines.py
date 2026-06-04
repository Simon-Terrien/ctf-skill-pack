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
import re
import os
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from .contracts import Category, Task

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

    def _ensure_pipeline(self):
        if self._pipeline is None:
            from biobrain.runtime.pipeline import BioBrain  # lazy
            self._pipeline = BioBrain(identity_config=self._identity_config)
        return self._pipeline

    def _build_content(self, task: Task) -> str:
        arts = ", ".join(task.artifacts)
        fmt = f" Flag format: {task.flag_format}." if task.flag_format else ""
        return (
            f"CTF {self.category.value} task. Artifacts: {arts}.{fmt} "
            f"Recover the flag. Use only sandboxed tools. "
            f"Report the flag and how you reproduced it."
        )

    async def solve(self, task: Task) -> EngineResult:
        from biobrain.core.enums import InputSource  # lazy
        pipeline = self._ensure_pipeline()
        content = self._build_content(task)

        trace = await asyncio.to_thread(
            pipeline.process, content, InputSource.USER,
            {"session_id": task.challenge_id, "artifacts": task.artifacts},
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
        import json
        from pathlib import Path
        for art in task.artifacts:
            try:
                spec = json.loads(Path(art).read_text())
            except (OSError, ValueError):
                continue
            if "xor_key" not in spec or "blob_hex" not in spec:
                continue
            key = spec["xor_key"]
            blob = bytes.fromhex(spec["blob_hex"])
            flag = bytes(b ^ key for b in blob).decode("latin-1")
            # reproduce: re-encode and confirm it matches the stored blob
            reproduced = bytes(ord(c) ^ key for c in flag).hex() == spec["blob_hex"]
            return EngineResult(
                candidate=flag,
                evidence=[f"artifact={art}",
                          f"recovered via single-byte XOR key=0x{key:02x}",
                          "reproduction=re-encode(flag,key)==stored blob"],
                reproduced=reproduced,
                reproduction={"method": "reencode_xor", "artifact": art},
                technique=["xor", "keygen-inversion"],
                reasoning=["static scan found no plaintext flag",
                           "identified single-byte XOR transform",
                           "inverted transform to recover flag"],
            )
        return EngineResult(reasoning=["no recognizable transform in artifacts"])
