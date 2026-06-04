"""End-to-end test of the engine weld: a reverse challenge whose flag is NOT
plaintext in the artifact, so it bypasses the static scan and exercises the
SolveEngine -> Candidate -> Gate path.

Uses StubReverseEngine (deterministic, no LLM). Swapping in BioBrainAdapter is
a constructor change; the agent/gate wiring under test is identical.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from pathlib import Path

from ctfrt.agent import SpecialistAgent
from ctfrt.bus import InMemoryBus
from ctfrt.contracts import Candidate, Category, Task
from ctfrt.engines import StubReverseEngine, EngineResult
from ctfrt.gate import Gate
from ctfrt.memory import InMemoryWorkingMemory
from ctfrt.tools import Researcher


def make_xor_crackme(path: Path, flag: str, key: int = 0x5A) -> None:
    """Write a 'crackme' whose flag is XOR-obfuscated — not present as plaintext."""
    blob = bytes(ord(c) ^ key for c in flag)
    path.write_text(json.dumps({"xor_key": key, "blob_hex": blob.hex()}))


async def test_static_scan_cannot_find_xor_flag(tmp_path: Path):
    """Negative control: the cheap scan must MISS the obfuscated flag."""
    art = tmp_path / "crackme.json"
    make_xor_crackme(art, "CTF{xor_reversed}")
    agent = SpecialistAgent(Category.reverse, InMemoryBus(), InMemoryWorkingMemory(),
                            None, Researcher(), engine=None)
    assert agent._find_static_flag(
        Task(challenge_id="c", category=Category.reverse, artifacts=[str(art)],
             flag_format=r"CTF\{[^}]+\}")
    ) is None


async def test_engine_recovers_flag_and_gate_accepts(tmp_path: Path):
    """The weld: static scan fails -> engine reverses -> candidate -> gate solved."""
    art = tmp_path / "crackme.json"
    make_xor_crackme(art, "CTF{xor_reversed}")

    bus = InMemoryBus()
    mem = InMemoryWorkingMemory()
    agent = SpecialistAgent(Category.reverse, bus, mem, None, Researcher(),
                            engine=StubReverseEngine())
    gate = Gate(bus, mem)

    sub = bus.subscribe("ctf.candidates", group="test")
    read = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)

    await agent.handle(Task(challenge_id="ch", category=Category.reverse,
                            artifacts=[str(art)], flag_format=r"CTF\{[^}]+\}"))

    raw = await asyncio.wait_for(read, 1)
    cand = Candidate.model_validate(raw)
    assert cand.candidate == "CTF{xor_reversed}"
    assert cand.validation_level == "reproduced"      # engine verified by re-encoding

    verdict = await gate.evaluate(cand)
    assert verdict.status == "solved"                 # gate independently promotes


async def test_engine_handoff_routes(tmp_path: Path):
    """An engine that reclassifies emits a handoff, not a candidate."""
    class Reclassifier:
        category = Category.reverse
        async def solve(self, task): return EngineResult(
            handoff=Category.crypto, handoff_reason="RSA params extracted")

    bus = InMemoryBus()
    agent = SpecialistAgent(Category.reverse, bus, InMemoryWorkingMemory(),
                            None, Researcher(), engine=Reclassifier())
    sub = bus.subscribe("ctf.handoffs", group="test")
    read = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)
    await agent.handle(Task(challenge_id="ch", category=Category.reverse,
                            artifacts=["x"], flag_format=None))
    raw = await asyncio.wait_for(read, 1)
    assert raw["target"] == "crypto-attack"


async def test_gate_verifier_accepts_honest_reproduction(tmp_path: Path):
    """Gate independently re-derives from the artifact and confirms an honest flag."""
    from ctfrt.verify import Verifier
    art = tmp_path / "crackme.json"
    make_xor_crackme(art, "CTF{xor_reversed}")
    bus, mem = InMemoryBus(), InMemoryWorkingMemory()
    agent = SpecialistAgent(Category.reverse, bus, mem, None, Researcher(),
                            engine=StubReverseEngine())
    gate = Gate(bus, mem, verifier=Verifier())   # no runner needed for reencode_xor

    sub = bus.subscribe("ctf.candidates", group="t")
    read = asyncio.create_task(sub.__anext__()); await asyncio.sleep(0)
    await agent.handle(Task(challenge_id="ch", category=Category.reverse,
                            artifacts=[str(art)], flag_format=r"CTF\{[^}]+\}"))
    cand = Candidate.model_validate(await asyncio.wait_for(read, 1))
    verdict = await gate.evaluate(cand)
    assert verdict.status == "solved"


async def test_gate_verifier_catches_lying_engine(tmp_path: Path):
    """A LYING engine claims reproduced with a wrong flag. The gate re-derives
    truth from the artifact, the claim fails, and the candidate is rejected."""
    from ctfrt.verify import Verifier
    art = tmp_path / "crackme.json"
    make_xor_crackme(art, "CTF{real_flag}")

    class LyingEngine:
        category = Category.reverse
        async def solve(self, task):
            return EngineResult(
                candidate="CTF{fabricated}",          # wrong
                evidence=["totally legit, trust me"],
                reproduced=True,                       # false claim
                reproduction={"method": "reencode_xor", "artifact": str(art)},
            )

    bus, mem = InMemoryBus(), InMemoryWorkingMemory()
    agent = SpecialistAgent(Category.reverse, bus, mem, None, Researcher(),
                            engine=LyingEngine())
    gate = Gate(bus, mem, verifier=Verifier())

    sub = bus.subscribe("ctf.candidates", group="t")
    read = asyncio.create_task(sub.__anext__()); await asyncio.sleep(0)
    await agent.handle(Task(challenge_id="ch", category=Category.reverse,
                            artifacts=[str(art)], flag_format=r"CTF\{[^}]+\}"))
    cand = Candidate.model_validate(await asyncio.wait_for(read, 1))
    assert cand.validation_level == "reproduced"   # the engine claimed it
    verdict = await gate.evaluate(cand)
    assert verdict.status == "raw"                 # gate independently refused
    assert verdict.local_validation == "failed"


async def test_gate_verifier_sandbox_exec_with_fake_runner(tmp_path: Path):
    """sandbox_exec path: gate runs the artifact via an injected runner; the
    binary's exit code is ground truth, not the engine's word."""
    from ctfrt.verify import Verifier
    from ctfrt.contracts import SandboxResult

    async def fake_runner(req):
        # 'binary' accepts only the correct flag on stdin
        ok = req.stdin == b"CTF{exec_proven}"
        return SandboxResult(request_id=req.id, exit_code=0 if ok else 1)

    good = Candidate(challenge_id="ch", candidate="CTF{exec_proven}", source="x",
                     flag_format=r"CTF\{[^}]+\}", validation_level="reproduced",
                     evidence=["ran in sandbox"],
                     reproduction={"method": "sandbox_exec", "artifact": "chal", "expect_exit": 0})
    bad = good.model_copy(update={"candidate": "CTF{wrong}", "id": "other"})

    gate = Gate(InMemoryBus(), InMemoryWorkingMemory(), verifier=Verifier(runner=fake_runner))
    assert (await gate.evaluate(good)).status == "solved"
    assert (await gate.evaluate(bad)).status == "raw"


async def test_biobrain_adapter_supplies_required_constructor_args(tmp_path: Path):
    from ctfrt.engines import BioBrainAdapter

    captured = {}

    class FakeBioBrain:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_biobrain = types.ModuleType("biobrain")
    fake_runtime = types.ModuleType("biobrain.runtime")
    fake_pipeline = types.ModuleType("biobrain.runtime.pipeline")
    fake_pipeline.BioBrain = FakeBioBrain

    old_modules = {
        name: sys.modules.get(name)
        for name in ("biobrain", "biobrain.runtime", "biobrain.runtime.pipeline")
    }
    old_palace = os.environ.get("BIOBRAIN_PALACE_PATH")
    old_identity = os.environ.get("BIOBRAIN_IDENTITY_CONFIG")
    try:
        sys.modules["biobrain"] = fake_biobrain
        sys.modules["biobrain.runtime"] = fake_runtime
        sys.modules["biobrain.runtime.pipeline"] = fake_pipeline
        os.environ["BIOBRAIN_PALACE_PATH"] = str(tmp_path / "palace")
        os.environ["BIOBRAIN_IDENTITY_CONFIG"] = "env-identity.yaml"

        BioBrainAdapter(Category.reverse)._ensure_pipeline()
        assert captured["palace_path"] == str(tmp_path / "palace")
        assert captured["identity_config"] == "env-identity.yaml"
    finally:
        for name, mod in old_modules.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        if old_palace is None:
            os.environ.pop("BIOBRAIN_PALACE_PATH", None)
        else:
            os.environ["BIOBRAIN_PALACE_PATH"] = old_palace
        if old_identity is None:
            os.environ.pop("BIOBRAIN_IDENTITY_CONFIG", None)
        else:
            os.environ["BIOBRAIN_IDENTITY_CONFIG"] = old_identity


async def test_biobrain_adapter_timeout_returns_no_candidate(_tmp_path: Path):
    from ctfrt.engines import BioBrainAdapter
    import time

    class SlowPipeline:
        def process(self, *_args, **_kwargs):
            time.sleep(0.2)

    adapter = BioBrainAdapter(Category.reverse)
    adapter._ensure_pipeline = lambda: SlowPipeline()

    old_timeout = os.environ.get("CTF_AGENT_ENGINE_TIMEOUT_S")
    try:
        os.environ["CTF_AGENT_ENGINE_TIMEOUT_S"] = "0.1"
        result = await adapter.solve(Task(
            challenge_id="ch",
            category=Category.reverse,
            artifacts=["artifact"],
            flag_format=r"CTF\{[^}]+\}",
        ))
        assert result.candidate is None
        assert result.reasoning == ["timeout:0.1s"]
    finally:
        if old_timeout is None:
            os.environ.pop("CTF_AGENT_ENGINE_TIMEOUT_S", None)
        else:
            os.environ["CTF_AGENT_ENGINE_TIMEOUT_S"] = old_timeout


async def test_biobrain_adapter_solves_xor_artifact_before_pipeline(tmp_path: Path):
    from ctfrt.engines import BioBrainAdapter

    art = tmp_path / "xor_crackme.json"
    art.write_text(json.dumps({
        "type": "xor-crackme",
        "xor_key": 90,
        "blob_hex": bytes(ord(c) ^ 90 for c in "CTF{xor_reversed}").hex(),
    }))

    class ShouldNotRunPipeline:
        def process(self, *_args, **_kwargs):
            raise AssertionError("BioBrain pipeline should not run for xor-crackme")

    adapter = BioBrainAdapter(Category.reverse)
    adapter._ensure_pipeline = lambda: ShouldNotRunPipeline()
    result = await adapter.solve(Task(
        challenge_id="xor",
        category=Category.reverse,
        artifacts=[str(art)],
        flag_format=r"CTF\{[^}]+\}",
    ))
    assert result.candidate == "CTF{xor_reversed}"
    assert result.reproduced is True
    assert result.technique == ["xor", "keygen-inversion"]


TESTS = [
    test_static_scan_cannot_find_xor_flag,
    test_engine_recovers_flag_and_gate_accepts,
    test_engine_handoff_routes,
    test_gate_verifier_accepts_honest_reproduction,
    test_gate_verifier_catches_lying_engine,
    test_gate_verifier_sandbox_exec_with_fake_runner,
    test_biobrain_adapter_supplies_required_constructor_args,
    test_biobrain_adapter_timeout_returns_no_candidate,
    test_biobrain_adapter_solves_xor_artifact_before_pipeline,
]

if __name__ == "__main__":
    import tempfile
    for t in TESTS:
        with tempfile.TemporaryDirectory() as d:
            asyncio.run(t(Path(d)))
        print(f"PASS {t.__name__}")
