"""Orchestrator — the only holder of global board state.

Runs triage on new challenges, fans tasks out to specialists, consumes
hypotheses/handoffs/flags, dedups, and marks challenges solved only on a
verified verdict from the gate.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .bus import Bus
from .config import Topics
from .contracts import Candidate, Category, Challenge, Handoff, Hypothesis, Task, TraceEvent
from .log import get_logger, kv
from .memory import LongTermMemory, MemoryProtocol, NullLongTermMemory
from .workspace import normalize_relative_path, normalize_workdir, resolve_artifact_path


def route(triage: dict, hint: Category | None) -> Category:
    t = (triage.get("type") or "").lower()
    if any(k in t for k in ("elf", "pe", "mach-o", ".net", "wasm", "bytecode", "apk")):
        return Category.pwn if triage.get("memory_corruption") else Category.reverse
    if "pcap" in t or "disk" in t or "memory dump" in t:
        return Category.forensics
    if t in ("png", "jpeg", "gif", "wav", "mp3", "image", "audio"):
        return Category.stego
    if "http" in t or "url" in t:
        return Category.web
    if "cipher" in t or "rsa" in t or "modulus" in t or "crypto" in t:
        return Category.crypto
    return hint or Category.misc


def _classify_artifact(challenge_id: str, workdir: str, artifact: str) -> str:
    try:
        p = resolve_artifact_path(artifact, challenge_id=challenge_id, workdir=workdir)
    except ValueError:
        return "unknown"
    suffix = p.suffix.lower()
    try:
        head = p.read_bytes()[:16]
    except OSError:
        head = b""

    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith(b"MZ"):
        return "pe"
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return "mach-o"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"RIFF") and b"WAVE" in head:
        return "wav"
    if head[:4] in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x0a\x0d\x0d\x0a"):
        return "pcap"
    if head.startswith(b"PK\x03\x04"):
        return "apk" if suffix == ".apk" else "zip"
    if suffix in (".pcap", ".pcapng"):
        return "pcap"
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".wav", ".mp3"):
        return suffix.lstrip(".")
    if suffix in (".py", ".sh", ".js", ".rb", ".pl"):
        return "script"
    if suffix in (".txt", ".log", ".md"):
        return "text"
    return "unknown"


_MAX_HANDOFF_DEPTH = 3

log = get_logger(__name__)


class Orchestrator:
    def __init__(self, bus: Bus, mem: MemoryProtocol, ltm: LongTermMemory | None = None):
        self.bus = bus
        self.mem = mem
        self.ltm = ltm or NullLongTermMemory()

    def _validated_workspace(self, ch: Challenge) -> tuple[str, list[str]]:
        workdir = normalize_workdir(ch.workdir, ch.id)
        artifacts = [normalize_relative_path(artifact) for artifact in ch.artifacts]
        for artifact in artifacts:
            resolve_artifact_path(artifact, challenge_id=ch.id, workdir=workdir)
        return workdir, artifacts

    async def triage(self, ch: Challenge) -> dict:
        workdir, artifacts = self._validated_workspace(ch)
        lessons = await self.ltm.retrieve(signals=[ch.name, ch.description])
        artifact_types = [_classify_artifact(ch.id, workdir, artifact) for artifact in artifacts]
        desc = (ch.description or "").lower()
        remote = (ch.remote or "").lower()

        if ch.category_hint:
            primary = ch.category_hint.value
        elif any(t in ("elf", "pe", "mach-o", "apk", "script") for t in artifact_types):
            primary = next(t for t in artifact_types if t in ("elf", "pe", "mach-o", "apk", "script"))
        elif any(t in ("pcap",) for t in artifact_types):
            primary = "pcap"
        elif any(t in ("png", "jpeg", "gif", "wav", "mp3") for t in artifact_types):
            primary = next(t for t in artifact_types if t in ("png", "jpeg", "gif", "wav", "mp3"))
        elif remote.startswith(("http://", "https://")) or "http" in desc or "url" in desc:
            primary = "http"
        elif any(k in desc for k in ("rsa", "cipher", "modulus", "aes", "crypto")):
            primary = "crypto"
        else:
            primary = "unknown"

        return {"type": primary, "artifact_types": artifact_types, "lessons": lessons}

    async def on_challenge(self, ch: Challenge) -> None:
        try:
            workdir, artifacts = self._validated_workspace(ch)
        except ValueError as exc:
            log.warning("challenge rejected", extra=kv(
                challenge_id=ch.id, error=repr(exc)))
            await self.bus.publish(Topics.TRACES, TraceEvent(
                challenge_id=ch.id,
                kind="challenge_rejected",
                payload={"reason": str(exc)},
            ))
            log.debug("publish topic", extra=kv(
                topic=Topics.TRACES, challenge_id=ch.id, kind="challenge_rejected"))
            return
        ch = ch.model_copy(update={"workdir": workdir, "artifacts": artifacts})
        triage = await self.triage(ch)
        cat = route(triage, ch.category_hint)
        board = {"name": ch.name, "status": "in_progress", "primary": cat.value,
                 "workdir": workdir, "artifacts": ch.artifacts, "flags": [], "rejected_paths": []}
        await self.mem.set_board(ch.id, board)
        await self.bus.publish(Topics.tasks_for(cat), Task(
            challenge_id=ch.id, workdir=workdir, category=cat, artifacts=ch.artifacts,
            flag_format=ch.flag_format, triage=triage), key=cat.value)
        log.debug("publish topic", extra=kv(
            topic=Topics.tasks_for(cat), challenge_id=ch.id, category=cat.value))
        log.info("challenge routed", extra=kv(challenge_id=ch.id, category=cat.value))
        await self.bus.publish(Topics.TRACES, TraceEvent(
            challenge_id=ch.id, kind="routed", payload={"category": cat.value, "triage": triage}))
        log.debug("publish topic", extra=kv(
            topic=Topics.TRACES, challenge_id=ch.id, kind="routed"))

    async def on_handoff(self, h: Handoff) -> None:
        board = await self.mem.get_board(h.challenge_id)
        sig = f"{h.from_category.value}->{h.target.value}:{h.reason}"
        if await self.mem.is_rejected(h.challenge_id, sig):
            return
        if h.handoff_depth >= _MAX_HANDOFF_DEPTH:
            log.warning("handoff depth exceeded", extra=kv(
                challenge_id=h.challenge_id, depth=h.handoff_depth, target=h.target.value))
            await self.bus.publish(Topics.TRACES, TraceEvent(
                challenge_id=h.challenge_id,
                kind="handoff_depth_exceeded",
                payload={"depth": h.handoff_depth, "target": h.target.value,
                         "from": h.from_category.value, "reason": h.reason},
            ))
            return
        await self.bus.publish(Topics.tasks_for(h.target), Task(
            challenge_id=h.challenge_id, workdir=board.get("workdir", ""), category=h.target,
            artifacts=board.get("artifacts", []),
            triage={"carry": h.carry, "handoff_depth": h.handoff_depth + 1}),
            key=h.target.value)
        log.info("handoff routed", extra=kv(
            challenge_id=h.challenge_id, source=h.from_category.value,
            target=h.target.value, depth=h.handoff_depth))
        log.debug("publish topic", extra=kv(
            topic=Topics.tasks_for(h.target), challenge_id=h.challenge_id, category=h.target.value))

    async def on_flag(self, c: Candidate) -> None:
        board = await self.mem.get_board(c.challenge_id)
        if c.status == "solved":
            board["status"] = "solved"
            board.setdefault("flags", []).append(c.candidate)
            await self.mem.set_board(c.challenge_id, board)
            log.info("challenge SOLVED", extra=kv(challenge_id=c.challenge_id, status=c.status))
            await self.bus.publish(Topics.TRACES, TraceEvent(
                challenge_id=c.challenge_id, kind="solved",
                payload={"category": board.get("primary", ""),
                         "technique": c.technique, "source": c.source}))
            log.debug("publish topic", extra=kv(
                topic=Topics.TRACES, challenge_id=c.challenge_id, kind="solved"))
            # Record the lesson so future triage for similar challenges retrieves it.
            await self.ltm.consolidate(c.challenge_id, {
                "technique": c.technique,
                "source": c.source,
                "category": board.get("primary", ""),
                "evidence": " | ".join(c.evidence[:3]),
            })

    async def on_hypothesis(self, h: Hypothesis) -> None:
        await self.mem.upsert_hypothesis(h)

    async def run(self) -> None:
        log.info("orchestrator started", extra=kv())
        await asyncio.gather(
            self._loop(Topics.CHALLENGES, Challenge, self.on_challenge, "orch-ch"),
            self._loop(Topics.HANDOFFS, Handoff, self.on_handoff, "orch-ho"),
            self._loop(Topics.FLAGS, Candidate, self.on_flag, "orch-fl"),
            self._loop(Topics.HYPOTHESES, Hypothesis, self.on_hypothesis, "orch-hy"),
        )

    async def _loop(self, topic, model, handler, group):
        log.debug("subscribe topic", extra=kv(topic=topic, group=group))
        async for raw in self.bus.subscribe(topic, group=group):
            await handler(model.model_validate(raw))
