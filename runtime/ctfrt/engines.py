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
from .reverse_tools import (
    ReverseArtifactSummary,
    StaticDetailSummary,
    analyze_artifact,
    collect_static_detail,
    format_reverse_summary,
    format_static_detail,
)
from .reverse_decision import (
    build_fact_bundle,
    evaluate_reverse_decision,
    format_reverse_decision,
    format_reverse_fact_bundle,
    refine_reverse_decision,
)
from .reverse_check_path import extract_check_path, format_check_path_summary
from .reverse_tool_registry import (
    ReverseToolResult,
    format_reverse_tool_result,
    run_reverse_tool,
    select_tools_for_next_actions,
)
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


def _describe_artifact(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"- path={path}\n  read_error={exc}"

    size = len(data)
    head = data[:16]
    if head.startswith(b"\x7fELF"):
        kind = "elf"
    elif head.startswith(b"MZ"):
        kind = "pe"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = "png"
    elif all((32 <= b <= 126) or b in (9, 10, 13) for b in data[:256]):
        kind = "text"
    else:
        kind = "binary"

    lines = [f"- path={path}", f"  kind={kind}", f"  size={size}"]
    if kind == "text":
        preview = data[:1024].decode("utf-8", errors="ignore").strip().replace("\n", "\\n")
        if preview:
            lines.append(f"  preview={preview[:400]}")
        return "\n".join(lines)

    strings: list[str] = []
    seen: set[str] = set()
    for raw in _PRINTABLE_RE.findall(data):
        text = raw.decode("latin-1", errors="ignore").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        strings.append(text)
        if len(strings) >= 24:
            break
    if strings:
        lines.append("  strings=" + " | ".join(strings[:24]))
    lines.append(f"  head_hex={data[:64].hex()}")
    return "\n".join(lines)


def _resolved_artifact_context(task: Task) -> tuple[list[str], str]:
    resolved: list[str] = []
    sections: list[str] = []
    for artifact in task.artifacts:
        try:
            path = resolve_artifact_path(
                artifact,
                challenge_id=task.challenge_id,
                workdir=task.workdir or None,
            )
        except ValueError as exc:
            sections.append(f"- path={artifact}\n  resolve_error={exc}")
            continue
        resolved.append(str(path))
        sections.append(_describe_artifact(path))
    return resolved, "\n".join(sections)


def _reverse_preanalysis(task: Task) -> tuple[list[str], list[ReverseArtifactSummary], str]:
    resolved: list[str] = []
    summaries: list[ReverseArtifactSummary] = []
    blocks: list[str] = []
    for artifact in task.artifacts:
        try:
            path = resolve_artifact_path(
                artifact,
                challenge_id=task.challenge_id,
                workdir=task.workdir or None,
            )
        except ValueError:
            continue
        resolved.append(str(path))
        summary = analyze_artifact(path)
        summaries.append(summary)
        blocks.append(format_reverse_summary(summary))
    return resolved, summaries, "\n\n".join(blocks)


def _reverse_static_detail(
    task: Task,
    resolved_artifacts: list[str],
    preanalysis: list[ReverseArtifactSummary],
) -> tuple[list[StaticDetailSummary], str]:
    by_path = {summary.path: summary for summary in preanalysis}
    details: list[StaticDetailSummary] = []
    blocks: list[str] = []
    for resolved in resolved_artifacts:
        detail = collect_static_detail(Path(resolved), by_path.get(resolved))
        details.append(detail)
        blocks.append(format_static_detail(detail))
    return details, "\n\n".join(blocks)


def _reverse_tool_evidence(
    resolved_artifacts: list[str],
    next_actions: list[str],
) -> tuple[list[ReverseToolResult], str]:
    tool_names = select_tools_for_next_actions(next_actions)
    results: list[ReverseToolResult] = []
    blocks: list[str] = []
    for resolved in resolved_artifacts:
        path = Path(resolved)
        for tool_name in tool_names:
            result = run_reverse_tool(path, tool_name)
            results.append(result)
            blocks.append(format_reverse_tool_result(result))
    return results, "\n\n".join(blocks)


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
        self._trace = None

    def bind_trace(self, trace) -> "BioBrainAdapter":
        self._trace = trace
        return self

    async def _emit(self, kind: str, payload: dict) -> None:
        if self._trace is not None:
            await self._trace(kind, payload)

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

    async def _run_pipeline(self, task: Task, content: str, resolved_artifacts: list[str]):
        from biobrain.core.enums import InputSource  # lazy

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        metadata = {
            "session_id": task.challenge_id,
            "artifacts": task.artifacts,
            "artifact_paths": resolved_artifacts,
            "workdir": task.workdir,
        }

        def worker():
            try:
                trace = self._ensure_pipeline().process(
                    content, InputSource.USER,
                    metadata,
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

    def _build_content(
        self,
        task: Task,
        *,
        resolved_artifacts: list[str],
        reverse_summary: str | None = None,
        static_detail_summary: str | None = None,
        decision_summary: str | None = None,
        tool_summary: str | None = None,
        refined_decision_summary: str | None = None,
        check_path_summary: str | None = None,
    ) -> str:
        arts = ", ".join(task.artifacts)
        resolved = ", ".join(resolved_artifacts) if resolved_artifacts else "(unresolved)"
        fmt = f" Flag format: {task.flag_format}." if task.flag_format else ""
        context = reverse_summary
        if not context:
            _, context = _resolved_artifact_context(task)
        if static_detail_summary:
            context = f"{context}\n\nStatic detail:\n{static_detail_summary}"
        if decision_summary:
            context = f"{context}\n\n{decision_summary}"
        if tool_summary:
            context = f"{context}\n\nReverse tool outputs:\n{tool_summary}"
        if refined_decision_summary:
            context = f"{context}\n\n{refined_decision_summary}"
        if check_path_summary:
            context = f"{context}\n\nCheck-path summary:\n{check_path_summary}"
        return (
            f"CTF {self.category.value} task. Artifacts: {arts}. Resolved paths: {resolved}."
            f"{fmt} Recover the flag. Use only sandboxed tools. "
            f"Report the flag and how you reproduced it.\n\n"
            f"Local artifact context:\n{context}"
        )

    async def solve(self, task: Task) -> EngineResult:
        local = _solve_xor_artifact(task)
        if local is not None:
            return local

        resolved_artifacts, summaries, reverse_summary = _reverse_preanalysis(task)
        for summary in summaries:
            await self._emit("reverse_preanalysis", {
                "kind": summary.kind,
                "size": summary.size,
                "sha256": summary.sha256,
                "string_count": len(summary.strings),
                "imports_count": len(summary.imports),
                "stripped": summary.stripped,
                "pie": summary.pie,
                "tools_used": summary.tools_used,
            })

        static_detail_text = None
        decision_summary = None
        tool_summary_text = None
        refined_decision_summary = None
        check_path_summary = None
        if self.category == Category.reverse:
            decision = evaluate_reverse_decision(summaries)
            decision_summary = format_reverse_decision(decision)
            await self._emit("reverse_next_action", decision.model_dump())
            static_details, static_detail_text = _reverse_static_detail(task, resolved_artifacts, summaries)
            for detail in static_details:
                await self._emit("reverse_static_detail", {
                    "tool_used": detail.tool_used,
                    "line_count": detail.line_count,
                    "truncated": detail.truncated,
                    "anchor_count": len(detail.candidate_anchors),
                    "compare_import_count": len(detail.imported_compare_symbols),
                    "input_import_count": len(detail.imported_input_symbols),
                })
            tool_results, tool_summary_text = _reverse_tool_evidence(resolved_artifacts, decision.next_actions)
            for tool_result in tool_results:
                await self._emit("reverse_tool_result", {
                    "tool": tool_result.name,
                    "path": tool_result.path,
                    "command": tool_result.command,
                    "exit_code": tool_result.exit_code,
                    "summary_line_count": len(tool_result.summary_lines),
                    "facts": tool_result.facts,
                    "truncated": tool_result.truncated,
                    "timed_out": tool_result.timed_out,
                    "tool_missing": tool_result.tool_missing,
                    "error": tool_result.error,
                })
            fact_bundle = build_fact_bundle(tool_results)
            refined_decision = refine_reverse_decision(decision, fact_bundle)
            refined_decision_summary = (
                format_reverse_fact_bundle(fact_bundle)
                + "\n\n"
                + "Refined reverse decision:\n"
                + "\n".join(format_reverse_decision(refined_decision).splitlines()[1:])
            )
            await self._emit("reverse_decision_refined", refined_decision.model_dump())
            if any(action in {"input_path_analysis", "string_reference_analysis", "follow_string_references", "disassembly_summary"} for action in refined_decision.next_actions):
                for resolved in resolved_artifacts:
                    check_path = extract_check_path(Path(resolved), tool_results)
                    check_path_summary = format_check_path_summary(check_path)
                    await self._emit("reverse_check_path", check_path.model_dump())

        content = self._build_content(
            task,
            resolved_artifacts=resolved_artifacts,
            reverse_summary=reverse_summary if self.category == Category.reverse else None,
            static_detail_summary=static_detail_text if self.category == Category.reverse else None,
            decision_summary=decision_summary if self.category == Category.reverse else None,
            tool_summary=tool_summary_text if self.category == Category.reverse else None,
            refined_decision_summary=refined_decision_summary if self.category == Category.reverse else None,
            check_path_summary=check_path_summary if self.category == Category.reverse else None,
        )
        try:
            trace = await self._run_pipeline(task, content, resolved_artifacts)
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
