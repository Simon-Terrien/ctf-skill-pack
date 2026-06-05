"""Full-stack integration tests: Orchestrator + Gate + SpecialistAgent on InMemoryBus.

Each test wires up real components (no mocks) and exercises a distinct path through
the runtime. These prove end-to-end correctness that unit tests cannot.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ctfrt.agent import SpecialistAgent
from ctfrt.bus import InMemoryBus
from ctfrt.contracts import Candidate, Category, Challenge, Task, TraceEvent
from ctfrt.engines import EngineResult as ER, StubReverseEngine
from ctfrt.workspace import register_artifacts
import ctfrt.config as config
from ctfrt.gate import Gate
from ctfrt.memory import InMemoryWorkingMemory
from ctfrt.orchestrator import Orchestrator
from ctfrt.tools import Researcher


# ── helpers ───────────────────────────────────────────────────────────────────

def _xor_crackme(tmp_path: Path, flag: str = "CTF{xor_reversed}", key: int = 90) -> Path:
    blob = bytes(ord(c) ^ key for c in flag)
    art = tmp_path / "crackme.json"
    art.write_text(json.dumps({"type": "xor-crackme", "xor_key": key, "blob_hex": blob.hex()}))
    return art


def _register(challenge_id: str, artifact_paths: list[str], challenge_root: str) -> tuple[str, list[str]]:
    """Register artifacts into challenge workspace under challenge_root."""
    old_root = config.settings.challenge_root
    try:
        config.settings.challenge_root = challenge_root
        return register_artifacts(challenge_id, artifact_paths)
    finally:
        config.settings.challenge_root = old_root


async def _run_stack(
    challenge: Challenge,
    category: Category,
    *,
    engine=None,
    timeout: float = 3.0,
) -> tuple[dict | None, list[dict]]:
    """Start Orchestrator + Gate + one specialist, publish the challenge, collect result."""
    bus = InMemoryBus()
    mem = InMemoryWorkingMemory()
    researcher = Researcher()

    loops = [
        asyncio.create_task(Orchestrator(bus, mem).run()),
        asyncio.create_task(Gate(bus, mem).run()),
        asyncio.create_task(SpecialistAgent(
            category, bus, mem, None, researcher, engine=engine).run()),
    ]

    flags_sub = bus.subscribe("ctf.flags", group="test-flags")
    traces_sub = bus.subscribe("ctf.traces", group="test-traces")
    traces: list[dict] = []

    async def collect_traces():
        async for raw in traces_sub:
            traces.append(raw)

    flag_task = asyncio.create_task(flags_sub.__anext__())
    trace_task = asyncio.create_task(collect_traces())

    await asyncio.sleep(0.05)
    await bus.publish("ctf.challenges", challenge, key=challenge.id)

    flag_result = None
    try:
        flag_result = await asyncio.wait_for(flag_task, timeout)
    except (asyncio.TimeoutError, StopAsyncIteration):
        flag_task.cancel()

    await asyncio.sleep(0.05)
    trace_task.cancel()
    for loop in loops:
        loop.cancel()
    await asyncio.gather(*loops, trace_task, return_exceptions=True)
    return flag_result, traces


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_static_solve_full_loop(tmp_path: Path):
    """Text artifact with embedded flag → orchestrator routes → agent scans → gate solves."""
    art = tmp_path / "note.txt"
    art.write_text("CTF{static_integration_win} noise")
    challenge_root = str(tmp_path / "workspace")
    workdir, artifacts = _register("static-01", [str(art)], challenge_root)
    old_root = config.settings.challenge_root
    config.settings.challenge_root = challenge_root
    try:
        ch = Challenge(
            id="static-01", name="static-01",
            category_hint=Category.misc,
            workdir=workdir, artifacts=artifacts,
            flag_format=r"CTF\{[^}]+\}",
        )
        flag, traces = await _run_stack(ch, Category.misc)
    finally:
        config.settings.challenge_root = old_root
    assert flag is not None
    assert flag["candidate"] == "CTF{static_integration_win}"
    assert flag["status"] == "solved"
    kinds = [t["kind"] for t in traces]
    assert "routed" in kinds
    assert "candidate_emitted" in kinds


async def test_xor_solve_full_loop(tmp_path: Path):
    """XOR crackme → StubReverseEngine recovers flag → gate accepts with reproduced tier."""
    art = _xor_crackme(tmp_path)
    challenge_root = str(tmp_path / "workspace")
    workdir, artifacts = _register("xor-01", [str(art)], challenge_root)
    old_root = config.settings.challenge_root
    config.settings.challenge_root = challenge_root
    try:
        ch = Challenge(
            id="xor-01", name="xor-01",
            category_hint=Category.reverse,
            workdir=workdir, artifacts=artifacts,
            flag_format=r"CTF\{[^}]+\}",
        )
        flag, traces = await _run_stack(ch, Category.reverse, engine=StubReverseEngine())
    finally:
        config.settings.challenge_root = old_root
    assert flag is not None
    assert flag["candidate"] == "CTF{xor_reversed}"
    assert flag["status"] == "solved"
    assert flag["validation_level"] == "reproduced"


async def test_no_solve_timeout(tmp_path: Path):
    """Unknown binary with no engine → no candidate → timeout → no flag."""
    art = tmp_path / "opaque.bin"
    art.write_bytes(b"\x00" * 64)
    challenge_root = str(tmp_path / "workspace")
    workdir, artifacts = _register("timeout-01", [str(art)], challenge_root)
    old_root = config.settings.challenge_root
    config.settings.challenge_root = challenge_root
    try:
        ch = Challenge(
            id="timeout-01", name="timeout-01",
            category_hint=Category.reverse,
            workdir=workdir, artifacts=artifacts,
            flag_format=r"CTF\{[^}]+\}",
        )
        flag, traces = await _run_stack(ch, Category.reverse, engine=None, timeout=0.8)
    finally:
        config.settings.challenge_root = old_root
    assert flag is None
    kinds = [t["kind"] for t in traces]
    assert "needs_engine" in kinds or "task_started" in kinds


async def test_wrong_format_rejection(tmp_path: Path):
    """Engine finds candidate that doesn't match flag_format → gate rejects → no solved flag."""
    art = tmp_path / "opaque.bin"
    art.write_bytes(b"\x00" * 32)

    class WrongFormatEngine:
        category = Category.reverse
        async def solve(self, task):
            return ER(
                candidate="not-a-ctf-flag",
                evidence=["found something"],
                reproduced=False,
            )

    challenge_root = str(tmp_path / "workspace")
    workdir, artifacts = _register("fmt-01", [str(art)], challenge_root)
    old_root = config.settings.challenge_root
    config.settings.challenge_root = challenge_root
    try:
        ch = Challenge(
            id="fmt-01", name="fmt-01",
            category_hint=Category.reverse,
            workdir=workdir, artifacts=artifacts,
            flag_format=r"CTF\{[^}]+\}",
        )
        flag, traces = await _run_stack(ch, Category.reverse,
                                        engine=WrongFormatEngine(), timeout=1.5)
    finally:
        config.settings.challenge_root = old_root
    if flag is not None:
        assert flag["status"] != "solved"
    kinds = [t["kind"] for t in traces]
    assert "candidate_rejected" in kinds or "engine_no_candidate" in kinds


async def test_handoff_path(tmp_path: Path):
    """Reclassifying engine emits a handoff → trace records handoff event."""
    art = tmp_path / "mystery.bin"
    art.write_bytes(b"\x00" * 32)

    class ReclassifyEngine:
        category = Category.reverse
        async def solve(self, task):
            return ER(handoff=Category.crypto, handoff_reason="RSA parameters detected")

    challenge_root = str(tmp_path / "workspace")
    workdir, artifacts = _register("handoff-01", [str(art)], challenge_root)
    old_root = config.settings.challenge_root
    config.settings.challenge_root = challenge_root
    try:
        ch = Challenge(
            id="handoff-01", name="handoff-01",
            category_hint=Category.reverse,
            workdir=workdir, artifacts=artifacts,
            flag_format=r"CTF\{[^}]+\}",
        )
        flag, traces = await _run_stack(ch, Category.reverse,
                                        engine=ReclassifyEngine(), timeout=1.0)
    finally:
        config.settings.challenge_root = old_root
    assert flag is None
    kinds = [t["kind"] for t in traces]
    assert "handoff" in kinds
    handoff_ev = next(t for t in traces if t["kind"] == "handoff")
    assert handoff_ev["payload"]["target"] == "crypto-attack"


async def test_multi_challenge_isolation(tmp_path: Path):
    """Two simultaneous challenges do not cross-contaminate their flags."""
    art1 = tmp_path / "a.txt"
    art1.write_text("CTF{challenge_alpha} noise")
    art2 = tmp_path / "b.txt"
    art2.write_text("CTF{challenge_beta} noise")

    challenge_root = str(tmp_path / "workspace")
    workdir1, artifacts1 = _register("iso-alpha", [str(art1)], challenge_root)
    workdir2, artifacts2 = _register("iso-beta", [str(art2)], challenge_root)

    old_root = config.settings.challenge_root
    config.settings.challenge_root = challenge_root

    bus = InMemoryBus()
    mem = InMemoryWorkingMemory()
    researcher = Researcher()

    loops = [
        asyncio.create_task(Orchestrator(bus, mem).run()),
        asyncio.create_task(Gate(bus, mem).run()),
        asyncio.create_task(SpecialistAgent(
            Category.misc, bus, mem, None, researcher).run()),
    ]
    flags_sub = bus.subscribe("ctf.flags", group="iso-test")
    collected: list[dict] = []

    async def collect():
        async for raw in flags_sub:
            collected.append(raw)

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0.05)

    try:
        ch1 = Challenge(id="iso-alpha", name="iso-alpha", category_hint=Category.misc,
                        workdir=workdir1, artifacts=artifacts1, flag_format=r"CTF\{[^}]+\}")
        ch2 = Challenge(id="iso-beta", name="iso-beta", category_hint=Category.misc,
                        workdir=workdir2, artifacts=artifacts2, flag_format=r"CTF\{[^}]+\}")
        await bus.publish("ctf.challenges", ch1, key=ch1.id)
        await bus.publish("ctf.challenges", ch2, key=ch2.id)

        await asyncio.sleep(1.5)
    finally:
        config.settings.challenge_root = old_root
        collector.cancel()
        for loop in loops:
            loop.cancel()
        await asyncio.gather(*loops, collector, return_exceptions=True)

    assert len(collected) == 2
    flags_by_challenge = {f["challenge_id"]: f["candidate"] for f in collected}
    assert flags_by_challenge.get("iso-alpha") == "CTF{challenge_alpha}"
    assert flags_by_challenge.get("iso-beta") == "CTF{challenge_beta}"


async def test_post_solve_consolidate_records_lesson(tmp_path: Path):
    """After a challenge is marked solved, Orchestrator.on_flag() calls ltm.consolidate()."""
    from ctfrt.memory import NullLongTermMemory

    consolidated: list[tuple[str, dict]] = []

    class CaptureLTM(NullLongTermMemory):
        async def consolidate(self, challenge_id: str, lesson: dict) -> None:
            consolidated.append((challenge_id, lesson))

    art = tmp_path / "note.txt"
    art.write_text("CTF{consolidate_win} noise")
    challenge_root = str(tmp_path / "workspace")
    workdir, artifacts = _register("consol-01", [str(art)], challenge_root)
    old_root = config.settings.challenge_root
    config.settings.challenge_root = challenge_root
    ltm = CaptureLTM()
    try:
        bus = InMemoryBus()
        mem = InMemoryWorkingMemory()
        loops = [
            asyncio.create_task(Orchestrator(bus, mem, ltm=ltm).run()),
            asyncio.create_task(Gate(bus, mem).run()),
            asyncio.create_task(SpecialistAgent(
                Category.misc, bus, mem, None, Researcher(), ltm=ltm).run()),
        ]
        flags_sub = bus.subscribe("ctf.flags", group="consol-flags")
        flag_task = asyncio.create_task(flags_sub.__anext__())
        await asyncio.sleep(0.05)
        ch = Challenge(id="consol-01", name="consol-01", category_hint=Category.misc,
                       workdir=workdir, artifacts=artifacts, flag_format=r"CTF\{[^}]+\}")
        await bus.publish("ctf.challenges", ch, key=ch.id)
        try:
            await asyncio.wait_for(flag_task, 3.0)
        except (asyncio.TimeoutError, StopAsyncIteration):
            flag_task.cancel()
        await asyncio.sleep(0.1)
    finally:
        config.settings.challenge_root = old_root
        for loop in loops:
            loop.cancel()
        await asyncio.gather(*loops, return_exceptions=True)

    assert len(consolidated) == 1
    cid, lesson = consolidated[0]
    assert cid == "consol-01"
    assert "static-artifact-scan" in lesson.get("source", "")
    assert isinstance(lesson.get("technique"), list)
