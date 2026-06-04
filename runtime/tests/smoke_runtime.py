from __future__ import annotations

import io
import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

from ctfrt.agent import SpecialistAgent
from ctfrt.bus import InMemoryBus
from ctfrt.contracts import Candidate, Category, Challenge, SandboxRequest, SandboxResult, Task
from ctfrt.gate import Gate
from ctfrt.memory import InMemoryWorkingMemory
from ctfrt.orchestrator import Orchestrator
from ctfrt.sandbox import run_sandboxed
from ctfrt.tools import Researcher


def run(coro):
    return asyncio.run(coro)


async def test_gate_rejects_invalid_local_only_candidate():
    bus = InMemoryBus(); mem = InMemoryWorkingMemory(); gate = Gate(bus, mem)
    c = Candidate(
        challenge_id="ch1", candidate="not-a-flag", source="unit",
        flag_format=r"CTF\{[^}]+\}", local_validation="passed",
        oracle_validation="not_available", validation_level="reproduced",
        evidence=["unit reproduction"],
    )
    out = await gate.evaluate(c)
    assert out.status == "raw"


async def test_gate_rejects_oracle_failed_even_if_local_passed():
    bus = InMemoryBus(); mem = InMemoryWorkingMemory(); gate = Gate(bus, mem)
    c = Candidate(
        challenge_id="ch1", candidate="CTF{bad}", source="unit",
        flag_format=r"CTF\{[^}]+\}", local_validation="passed",
        oracle_validation="failed", validation_level="reproduced",
        evidence=["unit reproduction"],
    )
    out = await gate.evaluate(c)
    assert out.status == "raw"


async def test_gate_rejects_observed_candidate_even_with_format_match():
    bus = InMemoryBus(); mem = InMemoryWorkingMemory(); gate = Gate(bus, mem)
    c = Candidate(
        challenge_id="ch1", candidate="CTF{only_seen}", source="unit",
        flag_format=r"CTF\{[^}]+\}", local_validation="not_attempted",
        oracle_validation="not_available", validation_level="observed",
        evidence=["seen in notes"],
    )
    out = await gate.evaluate(c)
    assert out.status == "raw"


async def test_gate_accepts_valid_local_candidate():
    bus = InMemoryBus(); mem = InMemoryWorkingMemory(); gate = Gate(bus, mem)
    c = Candidate(
        challenge_id="ch1", candidate="CTF{ok}", source="unit",
        flag_format=r"CTF\{[^}]+\}", local_validation="passed",
        oracle_validation="not_available", validation_level="reproduced",
        evidence=["unit reproduction"],
    )
    out = await gate.evaluate(c)
    assert out.status == "solved"


def test_sandbox_binary_json_roundtrip():
    res = SandboxResult(request_id="r", exit_code=0, stdout=b"\xff\x00abc", stderr=b"\xfe")
    raw = res.model_dump_json()
    parsed = SandboxResult.model_validate_json(raw)
    assert parsed.stdout == b"\xff\x00abc"
    assert parsed.stderr == b"\xfe"


async def test_sandbox_rejects_artifact_path_traversal():
    res = await run_sandboxed(SandboxRequest(challenge_id="c", artifact="../bin/sh"))
    assert res.exit_code == -126
    assert b"unsafe" in res.stderr


async def test_inmemory_bus_replays_late_subscriber():
    from ctfrt.contracts import TraceEvent
    bus = InMemoryBus()
    await bus.publish("topic", TraceEvent(challenge_id="c", kind="k"))
    sub = bus.subscribe("topic", group="g")
    item = await asyncio.wait_for(sub.__anext__(), timeout=1)
    assert item["challenge_id"] == "c"


async def test_inmemory_bus_fanout_by_group_and_balances_within_group():
    from ctfrt.contracts import TraceEvent
    bus = InMemoryBus()
    a1 = bus.subscribe("topic", group="same")
    a2 = bus.subscribe("topic", group="same")
    b1 = bus.subscribe("topic", group="other")
    # prime subscriptions
    t1 = asyncio.create_task(a1.__anext__()); t2 = asyncio.create_task(a2.__anext__()); t3 = asyncio.create_task(b1.__anext__())
    await asyncio.sleep(0)
    await bus.publish("topic", TraceEvent(challenge_id="c1", kind="k"))
    await bus.publish("topic", TraceEvent(challenge_id="c2", kind="k"))
    got_same = [await asyncio.wait_for(t1, 1), await asyncio.wait_for(t2, 1)]
    got_other_first = await asyncio.wait_for(t3, 1)
    assert {x["challenge_id"] for x in got_same} == {"c1", "c2"}
    assert got_other_first["challenge_id"] == "c1"


async def test_orchestrator_triage_classifies_elf_magic(tmp_path: Path):
    artifact = tmp_path / "a.bin"
    artifact.write_bytes(b"\x7fELF" + b"x" * 20)
    orch = Orchestrator(InMemoryBus(), InMemoryWorkingMemory())
    triage = await orch.triage(Challenge(name="elf", artifacts=[str(artifact)]))
    assert triage["type"] == "elf"


async def test_orchestrator_publishes_to_category_task_topic(tmp_path: Path):
    artifact = tmp_path / "a.bin"
    artifact.write_bytes(b"\x7fELF" + b"x" * 20)
    bus = InMemoryBus(); mem = InMemoryWorkingMemory(); orch = Orchestrator(bus, mem)
    sub_reverse = bus.subscribe("ctf.tasks.reverse", group="test")
    read_task = asyncio.create_task(sub_reverse.__anext__())
    await asyncio.sleep(0)
    await orch.on_challenge(Challenge(name="elf", artifacts=[str(artifact)]))
    raw = await asyncio.wait_for(read_task, 1)
    assert raw["category"] == "reverse"


async def test_specialist_static_flag_scan_emits_candidate(tmp_path: Path):
    artifact = tmp_path / "note.txt"
    artifact.write_text("noise CTF{static_win} end")
    bus = InMemoryBus(); mem = InMemoryWorkingMemory()
    agent = SpecialistAgent(Category.misc, bus, mem, None, Researcher())
    sub = bus.subscribe("ctf.candidates", group="test")
    read_task = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)
    await agent.handle(Task(challenge_id="ch", category=Category.misc, artifacts=[str(artifact)], flag_format=r"CTF\{[^}]+\}"))
    raw = await asyncio.wait_for(read_task, 1)
    c = Candidate.model_validate(raw)
    assert c.candidate == "CTF{static_win}"
    assert c.validation_level == "reproduced"


async def test_specialist_tool_call_traces_emit_on_engine_path(tmp_path: Path):
    from ctfrt.contracts import TraceEvent
    from ctfrt.engines import StubReverseEngine

    artifact = tmp_path / "crackme.json"
    artifact.write_text(json.dumps({"xor_key": 90, "blob_hex": bytes(ord(c) ^ 90 for c in "CTF{xor_reversed}").hex()}))
    bus = InMemoryBus(); mem = InMemoryWorkingMemory()
    agent = SpecialistAgent(Category.reverse, bus, mem, None, Researcher(), engine=StubReverseEngine())
    sub = bus.subscribe("ctf.traces", group="tool-audit")
    events = []
    task = asyncio.create_task(agent.handle(Task(
        challenge_id="ch", category=Category.reverse, artifacts=[str(artifact)],
        flag_format=r"CTF\{[^}]+\}",
    )))
    for _ in range(6):
        events.append(TraceEvent.model_validate(await asyncio.wait_for(sub.__anext__(), 1)))
        if any(ev.kind == "tool_call_started" for ev in events) and any(ev.kind == "tool_call_finished" for ev in events):
            break
    await task

    kinds = [ev.kind for ev in events]
    assert "tool_call_started" in kinds
    assert "tool_call_finished" in kinds
    assert any(ev.payload.get("tool") == "researcher.lookup" for ev in events if ev.kind.startswith("tool_call"))


async def test_researcher_tool_call_failed_trace():
    from ctfrt.tools import Researcher
    from ctfrt.contracts import TraceEvent

    async def boom(_query: str):
        raise RuntimeError("search backend down")

    seen = []
    async def trace(kind: str, payload: dict):
        seen.append(TraceEvent(challenge_id="ch", kind=kind, payload=payload))

    r = Researcher(local_search=boom, trace=trace)
    try:
        await r.lookup("question", tokens=["alpha"])
    except RuntimeError:
        pass

    kinds = [ev.kind for ev in seen]
    assert kinds[0] == "tool_call_started"
    assert "tool_call_failed" in kinds


async def test_gate_emits_acceptance_trace():
    from ctfrt.contracts import TraceEvent

    bus = InMemoryBus()
    mem = InMemoryWorkingMemory()
    gate = Gate(bus, mem)
    sub = bus.subscribe("ctf.traces", group="trace-test")
    read = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)

    c = Candidate(
        challenge_id="ch1", candidate="CTF{ok}", source="unit",
        flag_format=r"CTF\{[^}]+\}", local_validation="passed",
        oracle_validation="not_available", validation_level="reproduced",
        evidence=["unit reproduction"],
        technique=["xor"],
    )
    await gate.evaluate(c)

    raw = await asyncio.wait_for(read, 1)
    ev = TraceEvent.model_validate(raw)
    assert ev.kind == "candidate_accepted"
    assert ev.payload["accepted"] is True
    assert ev.payload["technique"] == ["xor"]


async def test_sandbox_worker_traces_request_and_result():
    import ctfrt.run as runtime_run
    from ctfrt.contracts import SandboxRequest, SandboxResult, TraceEvent
    from ctfrt.config import Topics

    bus = InMemoryBus()

    async def fake_run_sandboxed(req):
        return SandboxResult(
            request_id=req.id,
            exit_code=0,
            stdout=b"ok",
            stderr=b"warn",
        )

    old_runner = runtime_run.run_sandboxed
    runtime_run.run_sandboxed = fake_run_sandboxed
    task = asyncio.create_task(runtime_run.sandbox_worker(bus))
    try:
        await asyncio.sleep(0)
        sub = bus.subscribe("ctf.traces", group="sandbox-trace-test")
        read_request = asyncio.create_task(sub.__anext__())
        await bus.publish(Topics.SANDBOX_REQ, SandboxRequest(
            challenge_id="ch1", artifact="bin", argv=["--flag"],
        ))

        ev1 = TraceEvent.model_validate(await asyncio.wait_for(read_request, 1))
        read_result = asyncio.create_task(sub.__anext__())
        ev2 = TraceEvent.model_validate(await asyncio.wait_for(read_result, 1))
        assert ev1.kind == "sandbox_request"
        assert ev2.kind == "sandbox_result"
        assert ev2.payload["stdout_len"] == 2
        assert "stdout_sha256" in ev2.payload
    finally:
        task.cancel()
        runtime_run.run_sandboxed = old_runner
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_trace_recorder_persists_solved_trace(tmp_path: Path):
    from ctfrt.contracts import TraceEvent
    from ctfrt.trace_recorder import TraceRecorder

    bus = InMemoryBus()
    recorder = TraceRecorder(bus, tmp_path)
    task = asyncio.create_task(recorder.run())
    await asyncio.sleep(0)

    await bus.publish("ctf.traces", TraceEvent(
        challenge_id="rev-001",
        category=Category.reverse,
        kind="solved",
        payload={
            "category": "reverse",
            "technique": ["ltrace", "strcmp"],
            "source": "reverse:BioBrainAdapter",
        },
    ))

    trace_file = tmp_path / "rev-001.jsonl"
    for _ in range(50):
        if trace_file.exists():
            break
        await asyncio.sleep(0.01)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    rows = trace_file.read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["challenge_id"] == "rev-001"
    assert row["payload"]["technique"] == ["ltrace", "strcmp"]


def test_trace_cli_show_and_export(tmp_path: Path):
    trace_file = tmp_path / "rev-001.jsonl"
    trace_file.write_text(
        '{"challenge_id":"rev-001","kind":"solved","payload":{"technique":["xor"],"source":"reverse:Stub"}}\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"
    out = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "show-trace",
        "--challenge-id", "rev-001",
        "--trace-dir", str(tmp_path),
    ], text=True, env=env).strip()
    assert out == "solved technique=xor source=reverse:Stub"

    exported = tmp_path / "copy.jsonl"
    result = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "export-trace",
        "--challenge-id", "rev-001",
        "--trace-dir", str(tmp_path),
        "--output", str(exported),
    ], text=True, env=env).strip()
    assert exported.read_text(encoding="utf-8").strip() == trace_file.read_text(encoding="utf-8").strip()
    assert result == str(exported)


async def test_runtime_optional_components_do_not_require_cms_by_default(tmp_path: Path):
    from ctfrt.run import _optional_components

    old_query = os.environ.pop("CTF_MEMORY_QUERY", None)
    old_trace = os.environ.get("CTF_TRACE_DIR")
    try:
        os.environ["CTF_TRACE_DIR"] = str(tmp_path)
        extras = _optional_components("all", InMemoryBus())
        names = [name for name, _ in extras]
        assert names == ["trace-recorder"]
    finally:
        if old_query is None:
            os.environ.pop("CTF_MEMORY_QUERY", None)
        else:
            os.environ["CTF_MEMORY_QUERY"] = old_query
        if old_trace is None:
            os.environ.pop("CTF_TRACE_DIR", None)
        else:
            os.environ["CTF_TRACE_DIR"] = old_trace


async def test_runtime_optional_memory_component_starts_when_cms_available(tmp_path: Path):
    try:
        import cms  # noqa: F401
    except Exception:
        print("SKIP test_runtime_optional_memory_component_starts_when_cms_available (cms not importable)")
        return

    from ctfrt.run import _optional_components

    old_query = os.environ.get("CTF_MEMORY_QUERY")
    old_db = os.environ.get("CTF_CMS_DB")
    try:
        os.environ["CTF_MEMORY_QUERY"] = "cms"
        os.environ["CTF_CMS_DB"] = str(tmp_path / "cms.sqlite")
        extras = _optional_components("memory", InMemoryBus())
        names = [name for name, _ in extras]
        assert "memory" in names
    finally:
        if old_query is None:
            os.environ.pop("CTF_MEMORY_QUERY", None)
        else:
            os.environ["CTF_MEMORY_QUERY"] = old_query
        if old_db is None:
            os.environ.pop("CTF_CMS_DB", None)
        else:
            os.environ["CTF_CMS_DB"] = old_db


def test_cli_solve_local_static_flag(tmp_path: Path):
    artifact = tmp_path / "note.txt"
    artifact.write_text("noise CTF{cli_static_win} end")
    cmd = [
        sys.executable, "-m", "ctfrt.cli", "solve-local",
        "--name", "cli", "--category", "misc",
        "--artifact", str(artifact), "--flag-format", r"CTF\{[^}]+\}",
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    assert out == "CTF{cli_static_win}"


async def test_cli_solve_local_uses_configured_engine(tmp_path: Path):
    from argparse import Namespace

    import ctfrt.engines as runtime_engines
    from ctfrt import cli as runtime_cli
    from ctfrt.contracts import Category
    from ctfrt.engines import EngineResult

    artifact = tmp_path / "xor_crackme.json"
    artifact.write_text(json.dumps({
        "xor_key": 90,
        "blob_hex": bytes(ord(c) ^ 90 for c in "CTF{xor_reversed}").hex(),
    }))

    class FakeBioBrainAdapter:
        def __init__(self, category, *args, **kwargs):
            self.category = category

        async def solve(self, task):
            return EngineResult(
                candidate="CTF{xor_reversed}",
                evidence=[f"artifact={task.artifacts[0]}", "stub engine"],
                reproduced=True,
                reproduction={"method": "reencode_xor", "artifact": task.artifacts[0]},
                technique=["xor"],
                reasoning=["stub engine"],
            )

    old_adapter = runtime_engines.BioBrainAdapter
    old_engine = os.environ.get("CTF_AGENT_ENGINE")
    old_trace_dir = os.environ.get("CTF_TRACE_DIR")
    trace_dir = tmp_path / "traces"
    try:
        runtime_engines.BioBrainAdapter = FakeBioBrainAdapter
        os.environ["CTF_AGENT_ENGINE"] = "biobrain"
        os.environ["CTF_TRACE_DIR"] = str(trace_dir)

        args = Namespace(
            name="xor",
            category=Category.reverse.value,
            artifact=[str(artifact)],
            flag_format=r"CTF\{[^}]+\}",
            remote=None,
            description="",
            timeout=2.0,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            await runtime_cli.solve_local(args)

        assert buf.getvalue().strip() == "CTF{xor_reversed}"
        events = iter_trace_events(trace_dir, "xor")
        kinds = [ev.kind for ev in events]
        assert "engine_dispatch" in kinds
        assert "needs_engine" not in kinds
        assert "candidate_emitted" in kinds
        assert "gate_verdict" in kinds or "candidate_accepted" in kinds
    finally:
        runtime_engines.BioBrainAdapter = old_adapter
        if old_engine is None:
            os.environ.pop("CTF_AGENT_ENGINE", None)
        else:
            os.environ["CTF_AGENT_ENGINE"] = old_engine
        if old_trace_dir is None:
            os.environ.pop("CTF_TRACE_DIR", None)
        else:
            os.environ["CTF_TRACE_DIR"] = old_trace_dir


TESTS = [
    test_gate_rejects_invalid_local_only_candidate,
    test_gate_rejects_oracle_failed_even_if_local_passed,
    test_gate_rejects_observed_candidate_even_with_format_match,
    test_gate_accepts_valid_local_candidate,
    test_sandbox_binary_json_roundtrip,
    test_sandbox_rejects_artifact_path_traversal,
    test_inmemory_bus_replays_late_subscriber,
    test_inmemory_bus_fanout_by_group_and_balances_within_group,
    test_orchestrator_triage_classifies_elf_magic,
    test_orchestrator_publishes_to_category_task_topic,
    test_specialist_static_flag_scan_emits_candidate,
    test_specialist_tool_call_traces_emit_on_engine_path,
    test_researcher_tool_call_failed_trace,
    test_gate_emits_acceptance_trace,
    test_sandbox_worker_traces_request_and_result,
    test_trace_recorder_persists_solved_trace,
    test_trace_cli_show_and_export,
    test_runtime_optional_components_do_not_require_cms_by_default,
    test_runtime_optional_memory_component_starts_when_cms_available,
    test_cli_solve_local_static_flag,
    test_cli_solve_local_uses_configured_engine,
]


if __name__ == "__main__":
    import tempfile
    for test in TESTS:
        if "tmp_path" in test.__code__.co_varnames:
            with tempfile.TemporaryDirectory() as d:
                result = test(Path(d))
                if asyncio.iscoroutine(result):
                    asyncio.run(result)
        else:
            result = test()
            if asyncio.iscoroutine(result):
                asyncio.run(result)
        print(f"PASS {test.__name__}")
