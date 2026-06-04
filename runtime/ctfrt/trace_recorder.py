"""Durable trace sink.

Subscribes to ``ctf.traces`` and appends each event to a JSONL file keyed by
challenge_id. This is intentionally best-effort and off the critical path:
recording failures are logged, not raised into the mission flow.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from .config import Topics
from .contracts import TraceEvent
from .log import get_logger, kv, sanitize

log = get_logger(__name__)


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "unknown"


def trace_path_for(trace_dir: str | Path, challenge_id: str) -> Path:
    return Path(trace_dir) / f"{_safe_filename(challenge_id)}.jsonl"


def trace_run_id(ev: TraceEvent) -> str | None:
    payload = ev.payload or {}
    run_id = payload.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def iter_trace_events(trace_dir: str | Path, challenge_id: str) -> list[TraceEvent]:
    path = trace_path_for(trace_dir, challenge_id)
    if not path.exists():
        return []
    events: list[TraceEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(TraceEvent.model_validate_json(line))
    return events


def latest_trace_run_id(trace_dir: str | Path, challenge_id: str) -> str | None:
    latest = None
    for ev in iter_trace_events(trace_dir, challenge_id):
        run_id = trace_run_id(ev)
        if run_id is not None:
            latest = run_id
    return latest


def filter_trace_events(events: list[TraceEvent], *, run_id: str | None = None) -> list[TraceEvent]:
    if run_id is None:
        return events
    return [ev for ev in events if trace_run_id(ev) == run_id]


def validate_trace_events(events: list[TraceEvent]) -> list[str]:
    errors: list[str] = []
    if not events:
        return ["trace is empty"]

    kinds = [ev.kind for ev in events]
    kind_set = set(kinds)

    if "routed" not in kind_set:
        errors.append("missing routed")
    if "task_started" not in kind_set:
        errors.append("missing task_started")

    terminal_engine_events = {"candidate_emitted", "engine_no_candidate", "engine_error", "handoff"}
    needs_engine_indexes = [idx for idx, kind in enumerate(kinds) if kind == "needs_engine"]
    for idx in needs_engine_indexes:
        if "task_started" in kinds[:idx] and not any(kind in terminal_engine_events for kind in kinds[idx + 1:]):
            errors.append("missing terminal engine event after needs_engine")
            break

    if "candidate_accepted" in kind_set and "solved" not in kind_set:
        errors.append("missing solved after candidate_accepted")

    if "solved" in kind_set and "candidate_accepted" not in kind_set:
        errors.append("missing candidate_accepted before solved")

    if "solved" in kind_set:
        summary = mission_trace_summary(events)
        technique = summary.get("technique") or []
        source = summary.get("source")
        if not technique and source in {None, "", "?"}:
            errors.append("missing technique or source in solved summary")

    if kind_set.issubset({"routed", "task_started", "needs_engine"}) and "needs_engine" in kind_set:
        errors.append("trace ends at needs_engine without terminal engine event")

    return errors


def mission_trace_summary(events: list[TraceEvent]) -> dict[str, object]:
    if not events:
        return {
            "challenge_id": "",
            "status": "UNKNOWN",
            "category": "?",
            "technique": [],
            "source": "?",
            "engine": "?",
            "tool_calls": 0,
            "candidates_emitted": 0,
            "accepted_candidates": 0,
            "rejected_candidates": 0,
            "final_event": "?",
        }

    challenge_id = events[0].challenge_id
    category = "?"
    technique: list[str] = []
    source = "?"
    engine = "?"
    tool_calls = 0
    candidates_emitted = 0
    accepted_candidates = 0
    rejected_candidates = 0
    final_event = events[-1].kind

    for ev in events:
        payload = ev.payload or {}
        if ev.kind == "routed" and payload.get("category"):
            category = str(payload["category"])
        elif ev.category is not None and category == "?":
            category = ev.category.value

        if ev.kind == "tool_call_started":
            tool_calls += 1
        elif ev.kind == "candidate_emitted":
            candidates_emitted += 1
        elif ev.kind == "candidate_accepted":
            accepted_candidates += 1
            if payload.get("technique"):
                technique = list(payload["technique"])
        elif ev.kind == "candidate_rejected":
            rejected_candidates += 1
        elif ev.kind == "engine_dispatch" and payload.get("engine"):
            engine = str(payload["engine"])
        elif ev.kind == "solved":
            if payload.get("technique"):
                technique = list(payload["technique"])
            if payload.get("source"):
                source = str(payload["source"])

    status = final_event.upper()
    if final_event == "solved":
        status = "SOLVED"
    elif final_event == "candidate_rejected":
        status = "REJECTED"
    elif final_event == "engine_no_candidate":
        status = "ENGINE_NO_CANDIDATE"
    elif final_event == "engine_error":
        status = "ENGINE_ERROR"

    if source != "?" and ":" in source and engine == "?":
        engine = source.split(":", 1)[1]

    return {
        "challenge_id": challenge_id,
        "status": status,
        "category": category,
        "technique": technique,
        "source": source,
        "engine": engine,
        "tool_calls": tool_calls,
        "candidates_emitted": candidates_emitted,
        "accepted_candidates": accepted_candidates,
        "rejected_candidates": rejected_candidates,
        "final_event": final_event,
    }


def summarize_trace_event(ev: TraceEvent) -> str:
    payload = ev.payload or {}
    bits = [ev.kind]
    run_id = trace_run_id(ev)
    if run_id:
        bits.insert(0, f"run={run_id}")
    if ev.kind in {"candidate_accepted", "gate_verdict"}:
        bits.append(f"status={payload.get('status', '?')}")
        if "accepted" in payload:
            bits.append(f"accepted={payload['accepted']}")
        if payload.get("technique"):
            bits.append(f"technique={','.join(payload['technique'])}")
    elif ev.kind.startswith("sandbox_"):
        bits.append(f"exit={payload.get('exit_code', '?')}")
        if "timed_out" in payload:
            bits.append(f"timed_out={payload['timed_out']}")
    elif ev.kind.startswith("tool_call_"):
        bits.append(f"tool={payload.get('tool', '?')}")
        if "ok" in payload:
            bits.append(f"ok={payload['ok']}")
    elif ev.kind == "solved":
        if payload.get("technique"):
            bits.append(f"technique={','.join(payload['technique'])}")
        if payload.get("source"):
            bits.append(f"source={payload['source']}")
    elif ev.kind == "candidate_rejected":
        reasons = payload.get("reasons", [])
        if reasons:
            bits.append(f"reasons={','.join(reasons)}")
    return " ".join(bits)


class TraceRecorder:
    def __init__(self, bus, trace_dir: str | Path = ".ctfrt/traces"):
        self.bus = bus
        self.trace_dir = Path(trace_dir)
        self._lock = asyncio.Lock()

    def _path_for(self, challenge_id: str) -> Path:
        return trace_path_for(self.trace_dir, challenge_id)

    def _append(self, ev: TraceEvent) -> None:
        payload = sanitize(ev.payload or {})
        ev = ev.model_copy(update={"payload": payload})
        path = self._path_for(ev.challenge_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(ev.model_dump_json())
            fh.write("\n")

    async def record(self, ev: TraceEvent) -> None:
        async with self._lock:
            self._append(ev)

    async def run(self) -> None:
        async for raw in self.bus.subscribe(Topics.TRACES, group="trace-recorder"):
            try:
                ev = TraceEvent.model_validate(raw)
                await self.record(ev)
            except Exception as exc:  # best-effort audit sink
                cid = raw.get("challenge_id", "?") if isinstance(raw, dict) else "?"
                log.warning("trace record failed", extra=kv(
                    challenge_id=cid, error=repr(exc)))
