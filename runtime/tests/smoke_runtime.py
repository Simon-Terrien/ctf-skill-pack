from __future__ import annotations

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
from ctfrt.trace_recorder import iter_trace_events
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
    exported_rows = [
        json.loads(line) for line in exported.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(exported_rows) == 1
    assert exported_rows[0]["challenge_id"] == "rev-001"
    assert exported_rows[0]["kind"] == "solved"
    assert exported_rows[0]["payload"] == {"technique": ["xor"], "source": "reverse:Stub"}
    assert result == str(exported)


def test_trace_cli_show_and_export_support_run_filters(tmp_path: Path):
    artifact = tmp_path / "note.txt"
    artifact.write_text("noise CTF{cli_static_win} end")
    trace_dir = tmp_path / "traces"

    first = _run_solve_local_subprocess(
        name="trace-repeat",
        artifact=artifact,
        flag_format=r"CTF\{[^}]+\}",
        timeout=5,
        trace_dir=trace_dir,
    )
    second = _run_solve_local_subprocess(
        name="trace-repeat",
        artifact=artifact,
        flag_format=r"CTF\{[^}]+\}",
        timeout=5,
        trace_dir=trace_dir,
    )
    assert first.returncode == 0
    assert second.returncode == 0

    events = iter_trace_events(trace_dir, "trace-repeat")
    run_ids = []
    for ev in events:
        run_id = ev.payload.get("run_id")
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
    assert len(run_ids) == 2
    first_run, second_run = run_ids
    assert first_run != second_run

    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"
    show_all = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "show-trace",
        "--challenge-id", "trace-repeat",
        "--trace-dir", str(trace_dir),
    ], text=True, env=env)
    assert f"run={first_run}" in show_all
    assert f"run={second_run}" in show_all

    show_latest = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "show-trace",
        "--challenge-id", "trace-repeat",
        "--trace-dir", str(trace_dir),
        "--latest",
    ], text=True, env=env)
    assert f"run={second_run}" in show_latest
    assert f"run={first_run}" not in show_latest

    show_first = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "show-trace",
        "--challenge-id", "trace-repeat",
        "--trace-dir", str(trace_dir),
        "--run-id", first_run,
    ], text=True, env=env)
    assert f"run={first_run}" in show_first
    assert f"run={second_run}" not in show_first

    latest_export = tmp_path / "latest.jsonl"
    exported_latest = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "export-trace",
        "--challenge-id", "trace-repeat",
        "--trace-dir", str(trace_dir),
        "--latest",
        "--output", str(latest_export),
    ], text=True, env=env).strip()
    assert exported_latest == str(latest_export)
    latest_events = latest_export.read_text(encoding="utf-8").splitlines()
    assert latest_events
    assert all(f'"run_id":"{second_run}"' in line for line in latest_events)


def test_trace_cli_summarize_trace(tmp_path: Path):
    trace_file = tmp_path / "xor-clean.jsonl"
    trace_file.write_text(
        "\n".join([
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "routed",
                "payload": {"category": "reverse", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "task_started",
                "category": "reverse",
                "payload": {"category": "reverse", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "tool_call_started",
                "payload": {"tool": "researcher.lookup", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "tool_call_finished",
                "payload": {"tool": "researcher.lookup", "ok": True, "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "engine_dispatch",
                "payload": {"engine": "BioBrainAdapter", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "candidate_emitted",
                "payload": {"source": "BioBrainAdapter", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "candidate_accepted",
                "payload": {
                    "accepted": True,
                    "status": "solved",
                    "technique": ["xor", "keygen-inversion"],
                    "run_id": "run-1",
                },
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "solved",
                "payload": {
                    "category": "reverse",
                    "technique": ["xor", "keygen-inversion"],
                    "source": "reverse:BioBrainAdapter",
                    "run_id": "run-1",
                },
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"
    out = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "summarize-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
    ], text=True, env=env)
    assert "Status: SOLVED" in out
    assert "Technique: xor,keygen-inversion" in out
    assert "Tool calls: 1" in out
    assert "Candidates emitted: 1" in out
    assert "Accepted candidates: 1" in out
    assert "Engine: BioBrainAdapter" in out


def test_trace_cli_summarize_trace_supports_run_filters(tmp_path: Path):
    trace_file = tmp_path / "xor-clean.jsonl"
    rows = [
        {"challenge_id": "xor-clean", "kind": "routed", "payload": {"category": "reverse", "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "task_started", "payload": {"category": "reverse", "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "engine_dispatch", "payload": {"engine": "StubEngine", "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "candidate_emitted", "payload": {"run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "candidate_accepted", "payload": {"accepted": True, "status": "solved", "technique": ["strings-analysis"], "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "solved", "payload": {"category": "reverse", "technique": ["strings-analysis"], "source": "reverse:StubEngine", "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "routed", "payload": {"category": "reverse", "run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "task_started", "payload": {"category": "reverse", "run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "tool_call_started", "payload": {"tool": "researcher.lookup", "run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "tool_call_finished", "payload": {"tool": "researcher.lookup", "ok": True, "run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "engine_dispatch", "payload": {"engine": "BioBrainAdapter", "run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "candidate_emitted", "payload": {"run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "candidate_accepted", "payload": {"accepted": True, "status": "solved", "technique": ["xor", "keygen-inversion"], "run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "solved", "payload": {"category": "reverse", "technique": ["xor", "keygen-inversion"], "source": "reverse:BioBrainAdapter", "run_id": "run-2"}},
    ]
    trace_file.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"

    latest = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "summarize-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
        "--latest",
    ], text=True, env=env)
    assert "Technique: xor,keygen-inversion" in latest
    assert "Engine: BioBrainAdapter" in latest
    assert "Tool calls: 1" in latest

    first = subprocess.check_output([
        sys.executable, "-m", "ctfrt.cli", "summarize-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
        "--run-id", "run-1",
    ], text=True, env=env)
    assert "Technique: strings-analysis" in first
    assert "Engine: StubEngine" in first
    assert "Tool calls: 0" in first


def test_trace_cli_validate_trace_valid_solved_returns_0(tmp_path: Path):
    trace_file = tmp_path / "xor-clean.jsonl"
    trace_file.write_text(
        "\n".join([
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "routed",
                "payload": {"category": "reverse", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "task_started",
                "payload": {"category": "reverse", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "needs_engine",
                "payload": {"run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "candidate_emitted",
                "payload": {"run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "candidate_accepted",
                "payload": {
                    "accepted": True,
                    "status": "solved",
                    "technique": ["xor", "keygen-inversion"],
                    "run_id": "run-1",
                },
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "solved",
                "payload": {
                    "technique": ["xor", "keygen-inversion"],
                    "source": "reverse:BioBrainAdapter",
                    "run_id": "run-1",
                },
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"
    result = subprocess.run([
        sys.executable, "-m", "ctfrt.cli", "validate-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
    ], text=True, capture_output=True, env=env)
    assert result.returncode == 0
    assert result.stdout.strip() == "TRACE VALID: xor-clean"


def test_trace_cli_validate_trace_needs_engine_only_returns_1(tmp_path: Path):
    trace_file = tmp_path / "xor-clean.jsonl"
    trace_file.write_text(
        "\n".join([
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "routed",
                "payload": {"category": "reverse", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "task_started",
                "payload": {"category": "reverse", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "needs_engine",
                "payload": {"run_id": "run-1"},
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"
    result = subprocess.run([
        sys.executable, "-m", "ctfrt.cli", "validate-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
    ], text=True, capture_output=True, env=env)
    assert result.returncode == 1
    assert "TRACE INVALID: xor-clean" in result.stdout
    assert "- missing terminal engine event after needs_engine" in result.stdout


def test_trace_cli_validate_trace_candidate_accepted_without_solved_returns_1(tmp_path: Path):
    trace_file = tmp_path / "xor-clean.jsonl"
    trace_file.write_text(
        "\n".join([
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "routed",
                "payload": {"category": "reverse", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "task_started",
                "payload": {"category": "reverse", "run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "candidate_emitted",
                "payload": {"run_id": "run-1"},
            }),
            json.dumps({
                "challenge_id": "xor-clean",
                "kind": "candidate_accepted",
                "payload": {
                    "accepted": True,
                    "status": "solved",
                    "run_id": "run-1",
                },
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"
    result = subprocess.run([
        sys.executable, "-m", "ctfrt.cli", "validate-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
    ], text=True, capture_output=True, env=env)
    assert result.returncode == 1
    assert "TRACE INVALID: xor-clean" in result.stdout
    assert "- missing solved after candidate_accepted" in result.stdout


def test_trace_cli_validate_trace_missing_trace_returns_2(tmp_path: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"
    result = subprocess.run([
        sys.executable, "-m", "ctfrt.cli", "validate-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
    ], text=True, capture_output=True, env=env)
    assert result.returncode == 2


def test_trace_cli_validate_trace_latest_validates_only_latest_run_id(tmp_path: Path):
    trace_file = tmp_path / "xor-clean.jsonl"
    rows = [
        {"challenge_id": "xor-clean", "kind": "routed", "payload": {"category": "reverse", "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "task_started", "payload": {"category": "reverse", "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "needs_engine", "payload": {"run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "candidate_emitted", "payload": {"run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "candidate_accepted", "payload": {"accepted": True, "status": "solved", "technique": ["strings-analysis"], "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "solved", "payload": {"technique": ["strings-analysis"], "source": "reverse:StubEngine", "run_id": "run-1"}},
        {"challenge_id": "xor-clean", "kind": "routed", "payload": {"category": "reverse", "run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "task_started", "payload": {"category": "reverse", "run_id": "run-2"}},
        {"challenge_id": "xor-clean", "kind": "needs_engine", "payload": {"run_id": "run-2"}},
    ]
    trace_file.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"

    latest = subprocess.run([
        sys.executable, "-m", "ctfrt.cli", "validate-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
        "--latest",
    ], text=True, capture_output=True, env=env)
    assert latest.returncode == 1
    assert "- missing terminal engine event after needs_engine" in latest.stdout

    first = subprocess.run([
        sys.executable, "-m", "ctfrt.cli", "validate-trace",
        "--challenge-id", "xor-clean",
        "--trace-dir", str(tmp_path),
        "--run-id", "run-1",
    ], text=True, capture_output=True, env=env)
    assert first.returncode == 0
    assert first.stdout.strip() == "TRACE VALID: xor-clean"


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


def _run_solve_local_subprocess(*, name: str, artifact: Path, flag_format: str,
                                timeout: float, trace_dir: Path | None = None,
                                engine: str | None = None):
    env = os.environ.copy()
    env["PYTHONPATH"] = "runtime"
    if engine is None:
        env.pop("CTF_AGENT_ENGINE", None)
    else:
        env["CTF_AGENT_ENGINE"] = engine
    if trace_dir is not None:
        env["CTF_TRACE_DIR"] = str(trace_dir)
    else:
        env.pop("CTF_TRACE_DIR", None)
    return subprocess.run([
        sys.executable, "-m", "ctfrt.cli", "solve-local",
        "--name", name,
        "--category", "misc",
        "--artifact", str(artifact),
        "--flag-format", flag_format,
        "--timeout", str(timeout),
    ], text=True, capture_output=True, timeout=10, env=env)


def test_cli_solve_local_subprocess_exits_cleanly(tmp_path: Path):
    artifact = tmp_path / "note.txt"
    artifact.write_text("noise CTF{cli_static_win} end")
    result = _run_solve_local_subprocess(
        name="cli-static-exit",
        artifact=artifact,
        flag_format=r"CTF\{[^}]+\}",
        timeout=5,
    )
    assert result.returncode == 0
    assert "CTF{cli_static_win}" in result.stdout


def test_cli_solve_local_persists_trace(tmp_path: Path):
    artifact = tmp_path / "note.txt"
    artifact.write_text("noise CTF{cli_static_win} end")
    trace_dir = tmp_path / "traces"
    result = _run_solve_local_subprocess(
        name="cli-static-exit",
        artifact=artifact,
        flag_format=r"CTF\{[^}]+\}",
        timeout=5,
        trace_dir=trace_dir,
    )
    assert result.returncode == 0
    trace_path = trace_dir / "cli-static-exit.jsonl"
    assert trace_path.exists()
    kinds = [ev.kind for ev in iter_trace_events(trace_dir, "cli-static-exit")]
    for kind in ("routed", "task_started", "candidate_emitted", "candidate_accepted", "solved"):
        assert kind in kinds


def test_cli_solve_local_unsolved_exits_without_hanging(tmp_path: Path):
    artifact = tmp_path / "no_flag.txt"
    artifact.write_text("noise only")
    result = _run_solve_local_subprocess(
        name="cli-unsolved",
        artifact=artifact,
        flag_format=r"CTF\{[^}]+\}",
        timeout=2,
    )
    assert result.returncode != 0
    combined = f"{result.stdout}\n{result.stderr}".lower()
    assert "timeout" in combined


def test_cli_solve_local_biobrain_solves_xor_artifact_first(tmp_path: Path):
    artifact = tmp_path / "xor_crackme.json"
    artifact.write_text(json.dumps({
        "type": "xor-crackme",
        "xor_key": 90,
        "blob_hex": bytes(ord(c) ^ 90 for c in "CTF{xor_reversed}").hex(),
    }))
    trace_dir = tmp_path / "traces"
    result = _run_solve_local_subprocess(
        name="xor-test",
        artifact=artifact,
        flag_format=r"CTF\{[^}]+\}",
        timeout=5,
        trace_dir=trace_dir,
        engine="biobrain",
    )
    assert result.returncode == 0
    assert "CTF{xor_reversed}" in result.stdout
    kinds = [ev.kind for ev in iter_trace_events(trace_dir, "xor-test")]
    for kind in ("engine_dispatch", "candidate_emitted", "candidate_accepted", "solved"):
        assert kind in kinds
    solved = next(ev for ev in iter_trace_events(trace_dir, "xor-test") if ev.kind == "solved")
    assert solved.payload["technique"] == ["xor", "keygen-inversion"]


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
    test_trace_cli_show_and_export_support_run_filters,
    test_trace_cli_validate_trace_valid_solved_returns_0,
    test_trace_cli_validate_trace_needs_engine_only_returns_1,
    test_trace_cli_validate_trace_candidate_accepted_without_solved_returns_1,
    test_trace_cli_validate_trace_missing_trace_returns_2,
    test_trace_cli_validate_trace_latest_validates_only_latest_run_id,
    test_runtime_optional_components_do_not_require_cms_by_default,
    test_runtime_optional_memory_component_starts_when_cms_available,
    test_cli_solve_local_subprocess_exits_cleanly,
    test_cli_solve_local_persists_trace,
    test_cli_solve_local_unsolved_exits_without_hanging,
    test_cli_solve_local_biobrain_solves_xor_artifact_first,
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
