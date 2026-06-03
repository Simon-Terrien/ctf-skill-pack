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
from .log import get_logger, kv

log = get_logger(__name__)


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "unknown"


def trace_path_for(trace_dir: str | Path, challenge_id: str) -> Path:
    return Path(trace_dir) / f"{_safe_filename(challenge_id)}.jsonl"


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


def summarize_trace_event(ev: TraceEvent) -> str:
    payload = ev.payload or {}
    bits = [ev.kind]
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
        path = self._path_for(ev.challenge_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(ev.model_dump_json())
            fh.write("\n")

    async def record(self, ev: TraceEvent) -> None:
        async with self._lock:
            await asyncio.to_thread(self._append, ev)

    async def run(self) -> None:
        async for raw in self.bus.subscribe(Topics.TRACES, group="trace-recorder"):
            try:
                ev = TraceEvent.model_validate(raw)
                await self.record(ev)
            except Exception as exc:  # best-effort audit sink
                cid = raw.get("challenge_id", "?") if isinstance(raw, dict) else "?"
                log.warning("trace record failed", extra=kv(
                    challenge_id=cid, error=repr(exc)))
