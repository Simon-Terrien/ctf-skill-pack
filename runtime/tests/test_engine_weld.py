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
import shutil
from pathlib import Path

from ctfrt.agent import SpecialistAgent
from ctfrt.bus import InMemoryBus
from ctfrt.contracts import Candidate, Category, Task
from ctfrt.engines import StubReverseEngine, EngineResult
from ctfrt.gate import Gate
from ctfrt.memory import InMemoryWorkingMemory
from ctfrt.reverse_check_path import extract_check_path
from ctfrt.reverse_transform_path import extract_transform_path
from ctfrt.reverse_decision import ReverseFactBundle, build_fact_bundle, evaluate_reverse_decision, refine_reverse_decision
from ctfrt.reverse_tool_registry import ReverseToolResult, format_reverse_tool_result, run_reverse_tool, select_tools_for_next_actions
from ctfrt.reverse_tools import analyze_artifact, collect_static_detail, follow_string_references
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


async def test_biobrain_adapter_passes_resolved_artifact_context_to_pipeline(tmp_path: Path):
    from ctfrt.engines import BioBrainAdapter
    import ctfrt.config as config
    from ctfrt.workspace import register_artifacts

    source = tmp_path / "selfkey-src"
    source.write_bytes(
        b"\x7fELF"
        + b"\x00" * 60
        + b"usage: %s <password>\x00Wrong password\x00You cracked the password\x00"
    )

    captured = {}

    class FakeCognitive:
        result = ""
        evidence = []
        reasoning_trace = []

    class FakeTrace:
        halted_at = None
        halt_reason = ""
        audit_summary = "no candidate"
        cognitive = FakeCognitive()
        action_results = []

    class CapturePipeline:
        def process(self, content, source, metadata):
            captured["content"] = content
            captured["source"] = source
            captured["metadata"] = metadata
            return FakeTrace()

    adapter = BioBrainAdapter(Category.reverse)
    adapter._ensure_pipeline = lambda: CapturePipeline()
    old_root = config.settings.challenge_root
    try:
        config.settings.challenge_root = str(tmp_path / "challenge-root")
        workdir, artifacts = register_artifacts("selfkey", [str(source)])
        art = Path(config.settings.challenge_root) / workdir / artifacts[0]

        await adapter.solve(Task(
            challenge_id="selfkey",
            workdir=workdir,
            category=Category.reverse,
            artifacts=artifacts,
            flag_format=r"CTF\{[^}]+\}",
        ))

        assert str(art) in captured["content"]
        assert "Wrong password" in captured["content"]
        assert "You cracked the password" in captured["content"]
        assert "sha256=" in captured["content"]
        assert "tools_used=embedded_strings" in captured["content"]
        assert "Static detail:" in captured["content"]
        assert "candidate_anchors=" in captured["content"]
        assert captured["metadata"]["artifact_paths"] == [str(art)]
        assert captured["metadata"]["artifacts"] == artifacts
        assert captured["metadata"]["workdir"] == workdir
    finally:
        config.settings.challenge_root = old_root


async def test_reverse_tools_fake_elf_extracts_embedded_strings(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(
        b"\x7fELF"
        + b"\x00" * 64
        + b"usage: %s <password>\x00Wrong password\x00You cracked the password\x00"
    )
    summary = analyze_artifact(art)
    assert summary.kind == "elf"
    assert summary.size == art.stat().st_size
    assert any("Wrong password" in value for value in summary.strings)
    assert any("You cracked the password" in value for value in summary.strings)
    assert "embedded_strings" in summary.tools_used


async def test_reverse_tools_handles_missing_readelf_and_objdump_gracefully(tmp_path: Path):
    import ctfrt.reverse_tools as reverse_tools

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 64 + b"puts\x00strcmp\x00")
    old_which = shutil.which
    old_run_tool = reverse_tools._run_tool
    try:
        shutil.which = lambda _name: None
        reverse_tools._run_tool = lambda _args: (_ for _ in ()).throw(AssertionError("tool runner should not be called"))
        summary = reverse_tools.analyze_artifact(art)
        detail = reverse_tools.collect_static_detail(art, summary)
        assert summary.kind == "elf"
        assert summary.imports == []
        assert summary.sections == []
        assert summary.tools_used == ["embedded_strings"]
        assert detail.tool_used == "none"
        assert detail.line_count == 0
        assert detail.disassembly_excerpt == ""
    finally:
        shutil.which = old_which
        reverse_tools._run_tool = old_run_tool


async def test_static_detail_fake_objdump_output_is_capped(tmp_path: Path):
    import ctfrt.reverse_tools as reverse_tools

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 64 + b"password\x00strcmp\x00read\x00")
    summary = analyze_artifact(art)
    old_which = shutil.which
    old_run_tool = reverse_tools._run_tool
    try:
        shutil.which = lambda name: f"/usr/bin/{name}" if name == "objdump" else None
        reverse_tools._run_tool = lambda _args: "\n".join(f"{idx:04x}: mov eax, eax" for idx in range(200))
        detail = reverse_tools.collect_static_detail(art, summary)
        assert detail.tool_used == "objdump"
        assert detail.truncated is True
        assert detail.line_count <= 80
        assert "mov eax, eax" in detail.disassembly_excerpt
    finally:
        shutil.which = old_which
        reverse_tools._run_tool = old_run_tool


async def test_static_detail_classifies_imports_and_anchors(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(
        b"\x7fELF"
        + b"\x00" * 64
        + b"usage: %s <password>\x00Wrong password\x00Success\x00"
    )
    summary = ReverseArtifactSummary(
        path=str(art),
        kind="elf",
        magic="7f454c46",
        size=art.stat().st_size,
        sha256="deadbeef",
        strings=["usage: %s <password>", "Wrong password", "Success"],
        imports=["strcmp", "memcmp", "read", "fgets", "puts"],
        sections=[],
        tools_used=["embedded_strings"],
    )
    detail = collect_static_detail(art, summary)
    assert detail.imported_compare_symbols == ["strcmp", "memcmp"]
    assert detail.imported_input_symbols == ["read", "fgets"]
    assert any("password" in value.lower() for value in detail.candidate_anchors)
    assert any("wrong" in value.lower() for value in detail.interesting_strings)


async def test_reverse_decision_success_failure_strings_trigger_follow_string_references(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = ReverseArtifactSummary(
        path=str(art),
        kind="elf",
        magic="7f454c46",
        size=art.stat().st_size,
        sha256="deadbeef",
        strings=["Wrong password", "Success"],
        imports=[],
        sections=[".text", ".rodata", ".symtab"],
        stripped=False,
        tools_used=["embedded_strings"],
    )

    result = evaluate_reverse_decision([summary])
    assert "success_failure_strings_present" in result.matched_rules
    assert "follow_string_references" in result.next_actions
    assert "string-anchor-analysis" in result.inferred_techniques


async def test_reverse_decision_compare_imports_trigger_string_reference_analysis(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = ReverseArtifactSummary(
        path=str(art),
        kind="elf",
        magic="7f454c46",
        size=art.stat().st_size,
        sha256="deadbeef",
        strings=[],
        imports=["strcmp@GLIBC_2.2.5", "memcmp"],
        sections=[".text", ".rodata", ".symtab"],
        stripped=False,
        tools_used=["embedded_strings"],
    )

    result = evaluate_reverse_decision([summary])
    assert "compare_imports_present" in result.matched_rules
    assert "string_reference_analysis" in result.next_actions
    assert "direct-compare" in result.inferred_techniques


async def test_reverse_decision_stripped_and_missing_symtab_trigger_disassembly_summary(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = ReverseArtifactSummary(
        path=str(art),
        kind="elf",
        magic="7f454c46",
        size=art.stat().st_size,
        sha256="deadbeef",
        strings=["opaque branch"],
        imports=[],
        sections=[".text", ".rodata"],
        stripped=True,
        tools_used=["embedded_strings"],
    )

    result = evaluate_reverse_decision([summary])
    assert "stripped_plus_no_symbols" in result.matched_rules
    assert "disassembly_summary" in result.next_actions
    assert "stripped-binary" in result.inferred_techniques


async def test_reverse_decision_no_matching_rules_returns_empty_result(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "note.txt"
    art.write_text("plain note")
    summary = ReverseArtifactSummary(
        path=str(art),
        kind="binary",
        magic="706c6169",
        size=art.stat().st_size,
        sha256="deadbeef",
        strings=["just noise"],
        imports=[],
        sections=[".data"],
        stripped=False,
        tools_used=["embedded_strings"],
    )

    result = evaluate_reverse_decision([summary])
    assert result.matched_rules == []
    assert result.inferred_techniques == []
    assert result.confidence == 0.0
    assert result.next_actions == []
    assert result.handoff_candidates == []
    assert result.dynamic_allowed is False


async def test_reverse_decision_refines_from_compare_import_facts(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    base = evaluate_reverse_decision([
        ReverseArtifactSummary(
            path=str(art),
            kind="elf",
            magic="7f454c46",
            size=art.stat().st_size,
            sha256="deadbeef",
            strings=[],
            imports=[],
            sections=[".text", ".rodata", ".symtab"],
            stripped=False,
            tools_used=["embedded_strings"],
        )
    ])
    facts = build_fact_bundle([
        ReverseToolResult(
            name="readelf_symbols",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            facts={"imported_symbols": ["strcmp"], "compare_imports": ["strcmp"], "input_imports": []},
        )
    ])
    refined = refine_reverse_decision(base, facts)
    assert "facts_compare_imports_present" in refined.matched_rules
    assert "string_reference_analysis" in refined.next_actions
    assert "direct-compare" in refined.inferred_techniques


async def test_reverse_decision_refines_from_input_import_facts(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    base = evaluate_reverse_decision([
        ReverseArtifactSummary(
            path=str(art),
            kind="elf",
            magic="7f454c46",
            size=art.stat().st_size,
            sha256="deadbeef",
            strings=[],
            imports=[],
            sections=[".text"],
            stripped=False,
            tools_used=["embedded_strings"],
        )
    ])
    facts = build_fact_bundle([
        ReverseToolResult(
            name="readelf_symbols",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            facts={"imported_symbols": ["read"], "input_imports": ["read"]},
        )
    ])
    refined = refine_reverse_decision(base, facts)
    assert "facts_input_imports_present" in refined.matched_rules
    assert "input_path_analysis" in refined.next_actions


async def test_reverse_decision_refines_from_rodata_ascii_hints(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    base = evaluate_reverse_decision([
        ReverseArtifactSummary(
            path=str(art),
            kind="elf",
            magic="7f454c46",
            size=art.stat().st_size,
            sha256="deadbeef",
            strings=[],
            imports=[],
            sections=[".text"],
            stripped=False,
            tools_used=["embedded_strings"],
        )
    ])
    facts = build_fact_bundle([
        ReverseToolResult(
            name="objdump_rodata",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            facts={"has_rodata": True, "ascii_hints": ["Wrong password"]},
        )
    ])
    refined = refine_reverse_decision(base, facts)
    assert "facts_rodata_ascii_hints" in refined.matched_rules
    assert "follow_string_references" in refined.next_actions


async def test_reverse_decision_refines_from_missing_symtab(tmp_path: Path):
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    base = evaluate_reverse_decision([
        ReverseArtifactSummary(
            path=str(art),
            kind="elf",
            magic="7f454c46",
            size=art.stat().st_size,
            sha256="deadbeef",
            strings=[],
            imports=[],
            sections=[".text"],
            stripped=False,
            tools_used=["embedded_strings"],
        )
    ])
    facts = build_fact_bundle([
        ReverseToolResult(
            name="readelf_sections",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            facts={"has_symtab": False},
        )
    ])
    refined = refine_reverse_decision(base, facts)
    assert "facts_symtab_missing" in refined.matched_rules
    assert "disassembly_summary" in refined.next_actions
    assert "stripped-binary" in refined.inferred_techniques


async def test_follow_string_references_returns_anchors_for_fake_elf(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32 + b"Wrong password\x00Success\x00")

    result = follow_string_references(art, ["Wrong password", "Success"])
    assert result.anchors == ["Wrong password", "Success"]
    assert any("Wrong password" in entry for entry in result.rodata_offsets)
    assert any("Success" in entry for entry in result.rodata_offsets)
    assert "embedded_offsets" in result.tool_used


async def test_follow_string_references_missing_objdump_and_readelf_degrades_cleanly(tmp_path: Path):
    import ctfrt.reverse_tools as reverse_tools

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_which = shutil.which
    try:
        shutil.which = lambda _name: None
        result = follow_string_references(art, ["Missing anchor"])
        assert result.anchors == ["Missing anchor"]
        assert result.rodata_offsets == []
        assert result.disassembly_hits == []
        assert result.nearby_instructions == []
        assert result.tool_used == "embedded_offsets"
        assert result.error == "no string references found"
    finally:
        shutil.which = old_which


async def test_extract_check_path_finds_compare_call(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = extract_check_path(
        art,
        [
            ReverseToolResult(
                name="objdump_disassembly",
                path=str(art),
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                stdout="\n".join([
                    "0000000000001140 <main>:",
                    "1148: e8 e3 fe ff ff call 1030 <strcmp@plt>",
                ]),
                facts={"compare_imports": ["strcmp"]},
            )
        ],
    )
    assert "strcmp" in summary.compare_symbols
    assert any("strcmp" in line for line in summary.candidate_calls)


async def test_extract_check_path_infers_compare_call_without_symbol_facts(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = extract_check_path(
        art,
        [
            ReverseToolResult(
                name="objdump_disassembly",
                path=str(art),
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                stdout="\n".join([
                    "0000000000001140 <main>:",
                    "1148: e8 e3 fe ff ff call 1070 <strcmp@plt>",
                ]),
                facts={},
            )
        ],
    )
    assert "strcmp" in summary.compare_symbols
    assert any("strcmp" in line for line in summary.candidate_calls)


async def test_extract_check_path_keeps_helper_window_before_compare(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = extract_check_path(
        art,
        [
            ReverseToolResult(
                name="objdump_disassembly",
                path=str(art),
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                stdout="\n".join([
                    "10bf: 48 89 ef mov rdi,rbp",
                    "10c2: e8 59 01 00 00 call 1220 <sub_1220>",
                    "10c7: 48 89 ef mov rdi,rbp",
                    "10ca: 48 89 c6 mov rsi,rax",
                    "10d0: e8 9b ff ff ff call 1070 <strcmp@plt>",
                ]),
                facts={"compare_imports": ["strcmp"]},
            )
        ],
    )
    assert any("call 1220 <sub_1220>" in window for window in summary.nearby_windows)
    assert any("call 1070 <strcmp@plt>" in window for window in summary.nearby_windows)


async def test_extract_check_path_keeps_plt_offset_helper_window(tmp_path: Path):
    """Stripped binary: helper labeled <sym@plt+0xNNN> must not be filtered as a PLT stub."""
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = extract_check_path(
        art,
        [
            ReverseToolResult(
                name="objdump_disassembly",
                path=str(art),
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                stdout="\n".join([
                    "10bf: 48 89 ef mov rdi,rbp",
                    "10c2: e8 59 01 00 00 call 1220 <__cxa_finalize@plt+0x180>",
                    "10c7: 48 89 ef mov rdi,rbp",
                    "10ca: 48 89 c6 mov rsi,rax",
                    "10d0: e8 9b ff ff ff call 1070 <strcmp@plt>",
                ]),
                facts={"compare_imports": ["strcmp"]},
            )
        ],
    )
    assert any("call 1220 <__cxa_finalize@plt+0x180>" in window for window in summary.nearby_windows)
    assert any("call 1070 <strcmp@plt>" in window for window in summary.nearby_windows)


async def test_extract_transform_path_plt_offset_label_is_not_filtered(tmp_path: Path):
    """Stripped binary: transform_path must resolve helper labeled <sym@plt+offset>."""
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    tool_results = [
        ReverseToolResult(
            name="objdump_disassembly",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            stdout="\n".join([
                "10c2: e8 59 01 00 00 call 1220 <__cxa_finalize@plt+0x180>",
                "10d0: e8 9b ff ff ff call 1070 <strcmp@plt>",
                "1220: 55 push rbp",
                "122e: e8 4d fe ff ff call 1080 <malloc@plt>",
                "1233: 66 0f 6f 05 25 0e 00 00 movdqa xmm0,XMMWORD PTR [rip+0xe25] # 2060",
                "1280: 32 17 xor dl,BYTE PTR [rdi]",
                "1289: 48 39 cf cmp rdi,rcx",
                "128c: 75 f2 jne 1280 <__cxa_finalize@plt+0x1e0>",
            ]),
            facts={},
        )
    ]
    check_path = extract_check_path(art, tool_results)
    summary = extract_transform_path(art, tool_results, check_path)
    assert any("__cxa_finalize@plt+0x180" in call for call in summary.helper_calls)
    assert "xor" in summary.operation_kinds
    assert summary.error != "no helper transform call found"


async def test_extract_check_path_finds_branch_window(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = extract_check_path(
        art,
        [
            ReverseToolResult(
                name="objdump_disassembly",
                path=str(art),
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                stdout="\n".join([
                    "0000000000001140 <main>:",
                    "1150: 85 c0 test eax,eax",
                    "1152: 75 0a jne 115e <main+0x1e>",
                ]),
                facts={},
            )
        ],
    )
    assert any("jne" in line for line in summary.candidate_branches)
    assert summary.nearby_windows


async def test_extract_check_path_includes_rodata_hints(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = extract_check_path(
        art,
        [
            ReverseToolResult(
                name="objdump_rodata",
                path=str(art),
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                facts={"ascii_hints": ["Wrong password", "Success"]},
            ),
            ReverseToolResult(
                name="objdump_disassembly",
                path=str(art),
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                stdout="0000000000001140 <main>:",
                facts={},
            ),
        ],
    )
    assert "Wrong password" in summary.rodata_hints
    assert "Success" in summary.rodata_hints


async def test_extract_check_path_missing_symbols_degrades_cleanly(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    summary = extract_check_path(
        art,
        [
            ReverseToolResult(
                name="objdump_disassembly",
                path=str(art),
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                stdout="0000000000001140 <sub_1140>:",
                facts={},
            )
        ],
    )
    assert summary.candidate_calls == []
    assert summary.error == "no named check path found"


async def test_extract_check_path_does_not_execute_or_read_artifact(tmp_path: Path):
    import pathlib

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_read_bytes = pathlib.Path.read_bytes
    try:
        pathlib.Path.read_bytes = lambda self: (_ for _ in ()).throw(AssertionError("artifact should not be read"))
        summary = extract_check_path(
            art,
            [
                ReverseToolResult(
                    name="objdump_disassembly",
                    path=str(art),
                    read_only=True,
                    sandbox_required=False,
                    timeout_s=1.0,
                    stdout="1150: 85 c0 test eax,eax\n1152: 75 0a jne 115e <main+0x1e>",
                    facts={},
                )
            ],
        )
        assert summary.candidate_branches
    finally:
        pathlib.Path.read_bytes = old_read_bytes


async def test_extract_transform_path_finds_helper_and_xor_loop(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    tool_results = [
        ReverseToolResult(
            name="objdump_disassembly",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            stdout="\n".join([
                "10c2: e8 59 01 00 00 call 1220 <sub_1220>",
                "10d0: e8 9b ff ff ff call 1070 <strcmp@plt>",
                "1220: 55 push rbp",
                "122e: e8 4d fe ff ff call 1080 <malloc@plt>",
                "1233: 66 0f 6f 05 25 0e 00 00 movdqa xmm0,XMMWORD PTR [rip+0xe25] # 2060",
                "1254: e8 f7 fd ff ff call 1050 <strlen@plt>",
                "1280: 32 17 xor dl,BYTE PTR [rdi]",
                "1289: 48 39 cf cmp rdi,rcx",
                "128c: 75 f2 jne 1280 <sub_1220+0x60>",
            ]),
            facts={},
        )
    ]
    check_path = extract_check_path(art, tool_results)
    summary = extract_transform_path(art, tool_results, check_path)
    assert "sub_1220" in summary.transform_functions
    assert "xor" in summary.operation_kinds
    assert "malloc" in summary.operation_kinds
    assert "strlen" in summary.operation_kinds
    assert summary.loop_indicators


async def test_extract_transform_path_degrades_cleanly_without_helper(tmp_path: Path):
    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    tool_results = [
        ReverseToolResult(
            name="objdump_disassembly",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            stdout="10d0: e8 9b ff ff ff call 1070 <strcmp@plt>",
            facts={},
        )
    ]
    check_path = extract_check_path(art, tool_results)
    summary = extract_transform_path(art, tool_results, check_path)
    assert summary.transform_functions == []
    assert summary.error in {"no helper transform call found", "helper transform function body not found"}


async def test_reverse_deterministic_self_xor_solver_recovers_candidate(tmp_path: Path):
    from ctfrt.engines import _solve_self_xor_compare
    from ctfrt.reverse_tools import ReverseArtifactSummary

    art = tmp_path / "fake-elf"
    art.write_bytes(
        b"\x7fELF" + b"\x00" * 64 + b"Wrong password\x00You cracked the password\x00Great job!!\x00"
    )
    summary = ReverseArtifactSummary(
        path=str(art),
        kind="elf",
        magic="7f454c46",
        size=art.stat().st_size,
        sha256="deadbeef",
        strings=["Wrong password", "You cracked the password", "Great job!!"],
        imports=["strcmp"],
        sections=[".text", ".rodata"],
        stripped=True,
        pie=True,
        tools_used=["embedded_strings"],
    )
    tool_results = [
        ReverseToolResult(
            name="objdump_rodata",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            stdout="\n".join([
                "Contents of section .rodata:",
                " 2060 79476e7d 6a61476b 6c6a7776 7f476879",
                " 2070 6a77767f 4768796b 6b6f776a 7c2d282f",
            ]),
            facts={"ascii_hints": ["Wrong password", "You cracked the password", "Great job!!"]},
        ),
        ReverseToolResult(
            name="objdump_disassembly",
            path=str(art),
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            stdout="\n".join([
                "10c2: e8 59 01 00 00 call 1220 <sub_1220>",
                "10d0: e8 9b ff ff ff call 1070 <strcmp@plt>",
                "1220: 55 push rbp",
                "1225: bf 1a 00 00 00 mov edi,0x1a",
                "122e: e8 4d fe ff ff call 1080 <malloc@plt>",
                "1233: 66 0f 6f 05 25 0e 00 00 movdqa xmm0,XMMWORD PTR [rip+0xe25] # 2060",
                "123e: c6 40 19 00 mov BYTE PTR [rax+0x19],0x0",
                "1245: 0f 11 00 movups XMMWORD PTR [rax],xmm0",
                "1248: 66 0f 6f 05 20 0e 00 00 movdqa xmm0,XMMWORD PTR [rip+0xe20] # 2070",
                "1250: 0f 11 40 09 movups XMMWORD PTR [rax+0x9],xmm0",
                "1254: e8 f7 fd ff ff call 1050 <strlen@plt>",
                "1280: 32 17 xor dl,BYTE PTR [rdi]",
                "1289: 48 39 cf cmp rdi,rcx",
                "128c: 75 f2 jne 1280 <sub_1220+0x60>",
            ]),
            facts={},
        ),
    ]
    check_path = extract_check_path(art, tool_results)
    transform_path = extract_transform_path(art, tool_results, check_path)
    result = _solve_self_xor_compare(
        Task(challenge_id="selfkey", category=Category.reverse, artifacts=["fake-elf"], workdir=str(tmp_path)),
        [summary],
        tool_results,
        check_path,
        transform_path,
    )
    assert result is not None
    assert result.candidate == "xFo|k`Fjmkvw~Fixjjnvk},)."
    assert result.reproduced is True
    assert result.reproduction["method"] == "sandbox_exec"
    assert result.reproduction["success_marker"] == "You cracked the password"


async def test_reverse_tool_missing_degrades_cleanly(tmp_path: Path):
    import ctfrt.reverse_tool_registry as registry

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_which = registry.which
    try:
        registry.which = lambda _name: None
        result = run_reverse_tool(art, "objdump_disassembly")
        assert result.tool_missing is True
        assert result.error == "missing tool: objdump"
        assert result.command == []
    finally:
        registry.which = old_which


async def test_reverse_tool_runner_uses_no_shell(tmp_path: Path):
    import ctfrt.reverse_tool_registry as registry

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_which = registry.which
    old_run = registry.subprocess.run
    seen = {}
    try:
        registry.which = lambda name: f"/usr/bin/{name}"

        def fake_run(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs

            class Result:
                returncode = 0
                stdout = "ok"
                stderr = ""

            return Result()

        registry.subprocess.run = fake_run
        result = run_reverse_tool(art, "readelf_header")
        assert result.exit_code == 0
        assert seen["kwargs"]["shell"] is False
        assert seen["kwargs"]["timeout"] == 1.0
    finally:
        registry.which = old_which
        registry.subprocess.run = old_run


async def test_reverse_tool_never_executes_artifact_directly(tmp_path: Path):
    import ctfrt.reverse_tool_registry as registry

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_which = registry.which
    old_run = registry.subprocess.run
    seen = {}
    try:
        registry.which = lambda name: f"/usr/bin/{name}"

        def fake_run(*args, **kwargs):
            seen["command"] = args[0]

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        registry.subprocess.run = fake_run
        run_reverse_tool(art, "objdump_disassembly")
        assert seen["command"][0] != str(art)
        assert seen["command"][-1] == str(art)
    finally:
        registry.which = old_which
        registry.subprocess.run = old_run


async def test_reverse_tool_output_is_capped(tmp_path: Path):
    import ctfrt.reverse_tool_registry as registry

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_which = registry.which
    old_run = registry.subprocess.run
    try:
        registry.which = lambda name: f"/usr/bin/{name}"

        def fake_run(*args, **kwargs):
            class Result:
                returncode = 0
                stdout = "A" * 20000
                stderr = "B" * 20000

            return Result()

        registry.subprocess.run = fake_run
        result = run_reverse_tool(art, "objdump_disassembly")
        assert len(result.stdout) == 16000
        assert len(result.stderr) == 16000
        assert result.truncated is True
    finally:
        registry.which = old_which
        registry.subprocess.run = old_run


async def test_reverse_tool_result_extracts_summary_lines(tmp_path: Path):
    import ctfrt.reverse_tool_registry as registry

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_which = registry.which
    old_run = registry.subprocess.run
    try:
        registry.which = lambda name: f"/usr/bin/{name}"

        def fake_run(*args, **kwargs):
            class Result:
                returncode = 0
                stdout = "\n".join([
                    "Symbol table '.dynsym' contains 3 entries:",
                    "   Num:    Value          Size Type    Bind   Vis      Ndx Name",
                    "     0: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND strcmp@GLIBC_2.2.5",
                    "     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND memcmp@GLIBC_2.2.5",
                ])
                stderr = ""

            return Result()

        registry.subprocess.run = fake_run
        result = run_reverse_tool(art, "readelf_symbols")
        assert any("strcmp" in line for line in result.summary_lines)
        assert any("memcmp" in line for line in result.summary_lines)
    finally:
        registry.which = old_which
        registry.subprocess.run = old_run


async def test_reverse_tool_result_extracts_structured_facts(tmp_path: Path):
    import ctfrt.reverse_tool_registry as registry

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_which = registry.which
    old_run = registry.subprocess.run
    try:
        registry.which = lambda name: f"/usr/bin/{name}"

        def fake_run(*args, **kwargs):
            class Result:
                returncode = 0
                stdout = "\n".join([
                    "Class:                             ELF64",
                    "Type:                              DYN (Position-Independent Executable file)",
                    "Machine:                           Advanced Micro Devices X86-64",
                    "Entry point address:               0x1050",
                    "Requesting program interpreter:    /lib64/ld-linux-x86-64.so.2",
                ])
                stderr = ""

            return Result()

        registry.subprocess.run = fake_run
        result = run_reverse_tool(art, "readelf_header")
        assert result.facts["elf_class"] == "ELF64"
        assert result.facts["pie"] is True
        assert result.facts["entry_point"] == "0x1050"
        assert result.facts["dynamically_linked"] is True
    finally:
        registry.which = old_which
        registry.subprocess.run = old_run


async def test_reverse_tool_formatter_includes_summary_block(tmp_path: Path):
    import ctfrt.reverse_tool_registry as registry

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)
    old_which = registry.which
    old_run = registry.subprocess.run
    try:
        registry.which = lambda name: f"/usr/bin/{name}"

        def fake_run(*args, **kwargs):
            class Result:
                returncode = 0
                stdout = "ELF 64-bit LSB pie executable, x86-64"
                stderr = ""

            return Result()

        registry.subprocess.run = fake_run
        result = run_reverse_tool(art, "file_summary")
        formatted = format_reverse_tool_result(result)
        assert "summary:" in formatted
        assert "ELF 64-bit LSB pie executable, x86-64" in formatted
        assert "facts=" in formatted
        assert "stdout_excerpt:" in formatted
    finally:
        registry.which = old_which
        registry.subprocess.run = old_run


async def test_reverse_action_mapping_for_follow_string_references(_tmp_path: Path):
    tools = select_tools_for_next_actions(["follow_string_references"])
    assert tools == ["objdump_rodata", "objdump_disassembly"]


async def test_biobrain_adapter_emits_reverse_preanalysis_trace(tmp_path: Path):
    from ctfrt.engines import BioBrainAdapter
    import ctfrt.config as config
    from ctfrt.workspace import register_artifacts

    source = tmp_path / "selfkey-src"
    source.write_bytes(b"\x7fELF" + b"\x00" * 64 + b"puts\x00strcmp\x00Wrong password\x00")
    seen = []

    class FakeCognitive:
        result = ""
        evidence = []
        reasoning_trace = []

    class FakeTrace:
        halted_at = None
        halt_reason = ""
        audit_summary = "no candidate"
        cognitive = FakeCognitive()
        action_results = []

    class CapturePipeline:
        def process(self, _content, _source, _metadata):
            return FakeTrace()

    async def trace(kind: str, payload: dict):
        seen.append((kind, payload))

    adapter = BioBrainAdapter(Category.reverse).bind_trace(trace)
    adapter._ensure_pipeline = lambda: CapturePipeline()
    old_root = config.settings.challenge_root
    try:
        config.settings.challenge_root = str(tmp_path / "challenge-root")
        workdir, artifacts = register_artifacts("selfkey", [str(source)])
        await adapter.solve(Task(
            challenge_id="selfkey",
            workdir=workdir,
            category=Category.reverse,
            artifacts=artifacts,
            flag_format=r"CTF\{[^}]+\}",
        ))
    finally:
        config.settings.challenge_root = old_root

    event = next((payload for kind, payload in seen if kind == "reverse_preanalysis"), None)
    assert event is not None
    assert event["kind"] == "elf"
    assert event["string_count"] >= 1
    assert "embedded_strings" in event["tools_used"]
    static_event = next((payload for kind, payload in seen if kind == "reverse_static_detail"), None)
    assert static_event is not None
    assert "tool_used" in static_event
    assert "anchor_count" in static_event
    assert "compare_import_count" in static_event
    assert "input_import_count" in static_event
    next_action_event = next((payload for kind, payload in seen if kind == "reverse_next_action"), None)
    assert next_action_event is not None
    assert "follow_string_references" in next_action_event["next_actions"]
    assert "matched_rules" in next_action_event
    tool_events = [payload for kind, payload in seen if kind == "reverse_tool_result"]
    assert tool_events
    tool_names = [payload["tool"] for payload in tool_events]
    assert "objdump_rodata" in tool_names
    assert "objdump_disassembly" in tool_names
    assert any(payload["summary_line_count"] >= 0 for payload in tool_events)
    assert all("facts" in payload for payload in tool_events)
    refined_event = next((payload for kind, payload in seen if kind == "reverse_decision_refined"), None)
    assert refined_event is not None
    assert "matched_rules" in refined_event
    check_path_event = next((payload for kind, payload in seen if kind == "reverse_check_path"), None)
    assert check_path_event is not None
    assert "confidence" in check_path_event
    transform_path_event = next((payload for kind, payload in seen if kind == "reverse_transform_path"), None)
    assert transform_path_event is not None
    assert "confidence" in transform_path_event


async def test_reverse_tools_do_not_execute_artifact(tmp_path: Path):
    import ctfrt.reverse_tools as reverse_tools

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 64 + b"puts\x00strcmp\x00")
    old_which = shutil.which
    old_run_tool = reverse_tools._run_tool
    calls = []
    try:
        shutil.which = lambda name: f"/usr/bin/{name}" if name == "readelf" else None

        def fake_run_tool(args: list[str]) -> str:
            calls.append(args)
            assert Path(args[0]).name == "readelf"
            assert args[-1] == str(art)
            assert args[0] != str(art)
            return ""

        reverse_tools._run_tool = fake_run_tool
        summary = reverse_tools.analyze_artifact(art)
        detail = reverse_tools.collect_static_detail(art, summary)
        refs = reverse_tools.follow_string_references(art, ["puts", "strcmp"])
        assert summary.kind == "elf"
        assert calls
        assert detail.tool_used == "none"
        assert refs.tool_used.startswith("embedded_offsets+readelf")
    finally:
        shutil.which = old_which
        reverse_tools._run_tool = old_run_tool


async def test_reverse_registry_tools_do_not_execute_artifact(tmp_path: Path):
    import ctfrt.reverse_tool_registry as registry

    art = tmp_path / "fake-elf"
    art.write_bytes(b"\x7fELF" + b"\x00" * 64 + b"puts\x00strcmp\x00")
    old_which = registry.which
    old_run = registry.subprocess.run
    calls = []
    try:
        registry.which = lambda name: f"/usr/bin/{name}"

        def fake_run(args, **kwargs):
            calls.append(args)
            assert Path(args[0]).name in {"objdump", "readelf", "file", "checksec", "xxd"}
            assert args[0] != str(art)
            assert args[-1] == str(art) or any(str(art) in part for part in args)

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        registry.subprocess.run = fake_run
        run_reverse_tool(art, "objdump_rodata")
        assert calls
    finally:
        registry.which = old_which
        registry.subprocess.run = old_run


async def test_biobrain_prompt_contains_reverse_decision_result(tmp_path: Path):
    from ctfrt.engines import BioBrainAdapter
    import ctfrt.engines as engines
    import ctfrt.config as config
    from ctfrt.workspace import register_artifacts

    source = tmp_path / "selfkey-src"
    source.write_bytes(
        b"\x7fELF"
        + b"\x00" * 64
        + b"Wrong password\x00Success\x00strcmp\x00memcmp\x00"
    )

    captured = {}

    class FakeCognitive:
        result = ""
        evidence = []
        reasoning_trace = []

    class FakeTrace:
        halted_at = None
        halt_reason = ""
        audit_summary = "no candidate"
        cognitive = FakeCognitive()
        action_results = []

    class CapturePipeline:
        def process(self, content, source, metadata):
            captured["content"] = content
            captured["source"] = source
            captured["metadata"] = metadata
            return FakeTrace()

    adapter = BioBrainAdapter(Category.reverse)
    adapter._ensure_pipeline = lambda: CapturePipeline()
    old_run_reverse_tool = engines.run_reverse_tool
    old_root = config.settings.challenge_root
    try:
        def fake_run_reverse_tool(path: Path, tool_name: str) -> ReverseToolResult:
            return ReverseToolResult(
                name=tool_name,
                path=str(path),
                command=[f"/usr/bin/{tool_name}", str(path)],
                read_only=True,
                sandbox_required=False,
                timeout_s=1.0,
                exit_code=0,
                stdout="raw output",
                summary_lines=[f"{tool_name} summary line"],
                facts={"tool_name": tool_name},
            )

        engines.run_reverse_tool = fake_run_reverse_tool
        config.settings.challenge_root = str(tmp_path / "challenge-root")
        workdir, artifacts = register_artifacts("selfkey", [str(source)])

        await adapter.solve(Task(
            challenge_id="selfkey",
            workdir=workdir,
            category=Category.reverse,
            artifacts=artifacts,
            flag_format=r"CTF\{[^}]+\}",
        ))

        assert "Reverse decision result:" in captured["content"]
        assert "follow_string_references" in captured["content"]
        assert "string-anchor-analysis" in captured["content"]
        assert "matched_rules=success_failure_strings_present" in captured["content"]
        assert "Reverse tool outputs:" in captured["content"]
        assert "tool=objdump_rodata" in captured["content"]
        assert "tool=objdump_disassembly" in captured["content"]
        assert "summary:" in captured["content"]
        assert "facts=" in captured["content"]
        assert "Refined reverse decision:" in captured["content"]
        assert "Check-path summary:" in captured["content"]
        assert "Transform-path summary:" in captured["content"]
    finally:
        engines.run_reverse_tool = old_run_reverse_tool
        config.settings.challenge_root = old_root


async def test_biobrain_adapter_short_circuits_on_deterministic_reverse_candidate(tmp_path: Path):
    from ctfrt.engines import BioBrainAdapter
    import ctfrt.config as config
    import ctfrt.engines as engines
    from ctfrt.reverse_tools import StaticDetailSummary
    from ctfrt.workspace import register_artifacts

    source = tmp_path / "selfkey-src"
    source.write_bytes(
        b"\x7fELF" + b"\x00" * 64 + b"Wrong password\x00You cracked the password\x00Great job!!\x00"
    )

    tool_results = {
        "objdump_rodata": ReverseToolResult(
            name="objdump_rodata",
            path="",
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            stdout="\n".join([
                "Contents of section .rodata:",
                " 2060 79476e7d 6a61476b 6c6a7776 7f476879",
                " 2070 6a77767f 4768796b 6b6f776a 7c2d282f",
            ]),
            facts={"ascii_hints": ["Wrong password", "You cracked the password", "Great job!!"]},
        ),
        "objdump_disassembly": ReverseToolResult(
            name="objdump_disassembly",
            path="",
            read_only=True,
            sandbox_required=False,
            timeout_s=1.0,
            stdout="\n".join([
                "10c2: e8 59 01 00 00 call 1220 <sub_1220>",
                "10d0: e8 9b ff ff ff call 1070 <strcmp@plt>",
                "1220: 55 push rbp",
                "1225: bf 1a 00 00 00 mov edi,0x1a",
                "122e: e8 4d fe ff ff call 1080 <malloc@plt>",
                "1233: 66 0f 6f 05 25 0e 00 00 movdqa xmm0,XMMWORD PTR [rip+0xe25] # 2060",
                "123e: c6 40 19 00 mov BYTE PTR [rax+0x19],0x0",
                "1245: 0f 11 00 movups XMMWORD PTR [rax],xmm0",
                "1248: 66 0f 6f 05 20 0e 00 00 movdqa xmm0,XMMWORD PTR [rip+0xe20] # 2070",
                "1250: 0f 11 40 09 movups XMMWORD PTR [rax+0x9],xmm0",
                "1254: e8 f7 fd ff ff call 1050 <strlen@plt>",
                "1280: 32 17 xor dl,BYTE PTR [rdi]",
                "1289: 48 39 cf cmp rdi,rcx",
                "128c: 75 f2 jne 1280 <sub_1220+0x60>",
            ]),
            facts={"function_labels": [".text", "strcmp@plt"], "instruction_kinds": ["call", "cmp", "jne"]},
        ),
    }

    class ShouldNotRunPipeline:
        def process(self, *_args, **_kwargs):
            raise AssertionError("BioBrain pipeline should not run when deterministic reverse solver succeeds")

    adapter = BioBrainAdapter(Category.reverse)
    adapter._ensure_pipeline = lambda: ShouldNotRunPipeline()
    old_root = config.settings.challenge_root
    old_reverse_static_detail = engines._reverse_static_detail
    old_run_reverse_tool = engines.run_reverse_tool
    try:
        config.settings.challenge_root = str(tmp_path / "challenge-root")
        workdir, artifacts = register_artifacts("selfkey", [str(source)])
        resolved = str(Path(config.settings.challenge_root) / workdir / artifacts[0])

        def fake_reverse_static_detail(_task, _resolved_artifacts, _preanalysis):
            detail = StaticDetailSummary(
                path=resolved,
                tool_used="objdump",
                line_count=10,
                truncated=False,
                candidate_anchors=["Wrong password", "You cracked the password"],
            )
            return [detail], "detail"

        def fake_run_reverse_tool(path: Path, tool_name: str) -> ReverseToolResult:
            result = tool_results[tool_name].model_copy(deep=True)
            result.path = str(path)
            result.command = [f"/usr/bin/{tool_name}", str(path)]
            return result

        engines._reverse_static_detail = fake_reverse_static_detail
        engines.run_reverse_tool = fake_run_reverse_tool
        result = await adapter.solve(Task(
            challenge_id="selfkey",
            workdir=workdir,
            category=Category.reverse,
            artifacts=artifacts,
            flag_format=None,
        ))
        assert result.candidate == "xFo|k`Fjmkvw~Fixjjnvk},)."
        assert result.reproduction["method"] == "sandbox_exec"
        assert result.technique == ["direct-compare-xor"]
    finally:
        engines._reverse_static_detail = old_reverse_static_detail
        engines.run_reverse_tool = old_run_reverse_tool
        config.settings.challenge_root = old_root


async def test_crypto_engine_xor_brute_force_recovers_flag(tmp_path: Path):
    from ctfrt.engines import CryptoEngine

    flag = "CTF{xor_crypto_win}"
    key = 0xFF  # produces non-printable bytes → binary-cipher heuristic fires
    blob = bytes(ord(c) ^ key for c in flag)
    art = tmp_path / "challenge.bin"
    art.write_bytes(blob)

    engine = CryptoEngine()
    result = await engine.solve(Task(
        challenge_id="crypto-xor", category=Category.crypto,
        artifacts=[str(art)], flag_format=r"CTF\{[^}]+\}",
    ))
    assert result.candidate == flag
    assert result.reproduced is True
    assert "xor" in result.technique


async def test_crypto_engine_caesar_recovers_flag(tmp_path: Path):
    from ctfrt.engines import CryptoEngine

    def caesar_enc(s: str, shift: int) -> str:
        return "".join(
            chr((ord(c) - ord("A") + shift) % 26 + ord("A")) if c.isupper()
            else chr((ord(c) - ord("a") + shift) % 26 + ord("a")) if c.islower()
            else c
            for c in s
        )

    # caesar_hint requires ≥5 all-uppercase words with no lowercase
    plaintext = "CTF{CAESAR_SOLVED} HIDDEN MESSAGE INSIDE UPPERCASE TEXT"
    shift = 13
    ciphertext = caesar_enc(plaintext, shift)
    art = tmp_path / "caesar.txt"
    art.write_text(ciphertext)

    engine = CryptoEngine()
    result = await engine.solve(Task(
        challenge_id="crypto-caesar", category=Category.crypto,
        artifacts=[str(art)], flag_format=r"CTF\{[^}]+\}",
    ))
    assert result.candidate == "CTF{CAESAR_SOLVED}"
    assert "caesar" in result.technique


async def test_forensics_engine_finds_flag_in_strings(tmp_path: Path):
    from ctfrt.engines import ForensicsEngine

    art = tmp_path / "dump.bin"
    art.write_bytes(b"\x00" * 64 + b"CTF{forensics_string_win}\x00" + b"\x00" * 32)

    engine = ForensicsEngine()
    result = await engine.solve(Task(
        challenge_id="forensics-01", category=Category.forensics,
        artifacts=[str(art)], flag_format=r"CTF\{[^}]+\}",
    ))
    assert result.candidate == "CTF{forensics_string_win}"
    assert result.reproduced is True


async def test_crypto_decision_rsa_fields_trigger_rsa_analysis(tmp_path: Path):
    from ctfrt.crypto_decision import analyze_crypto_artifact, evaluate_crypto_decision

    text = "n = 123456789012345678901234567890\ne = 65537\nc = 987654321098765432109876543210"
    signals = analyze_crypto_artifact(text)
    decision = evaluate_crypto_decision(signals)
    assert "rsa_fields_present" in decision.matched_rules
    assert "rsa_analysis" in decision.next_actions
    assert "rsa" in decision.inferred_techniques


async def test_stego_decision_png_triggers_lsb_scan(tmp_path: Path):
    from ctfrt.stego_decision import analyze_stego_artifact, evaluate_stego_decision

    png_magic = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    signals = analyze_stego_artifact(png_magic, "image.png")
    decision = evaluate_stego_decision(signals)
    assert "png_image_detected" in decision.matched_rules
    assert "lsb_scan" in decision.next_actions
    assert "lsb" in decision.inferred_techniques


async def test_specialist_barren_loop_emits_hypothesis_and_no_candidate(tmp_path: Path):
    """Agent bounded loop: barren engine emits hypothesis ledger + engine_no_candidate."""
    art = tmp_path / "unknown.bin"
    art.write_bytes(b"\x00" * 32)

    barren_count = 0

    class BarrenEngine:
        category = Category.reverse
        async def solve(self, task):
            nonlocal barren_count
            barren_count += 1
            return EngineResult(
                reasoning=["no pattern found"],
                evidence=[f"attempt={barren_count}"],
            )

    bus = InMemoryBus()
    mem = InMemoryWorkingMemory()
    agent = SpecialistAgent(Category.reverse, bus, mem, None, Researcher(),
                            engine=BarrenEngine())

    traces = []
    sub = bus.subscribe("ctf.traces", group="test")

    async def collect():
        async for raw in sub:
            traces.append(raw)

    import asyncio
    collector = asyncio.create_task(collect())
    await agent.handle(Task(
        challenge_id="barren", category=Category.reverse,
        artifacts=[str(art)], flag_format=None,
    ))
    await asyncio.sleep(0.05)
    collector.cancel()

    kinds = [t["kind"] for t in traces]
    assert "engine_no_candidate" in kinds
    no_cand = next(t for t in traces if t["kind"] == "engine_no_candidate")
    assert no_cand["payload"]["hypothesis_count"] >= 1
    assert no_cand["payload"]["steps"] >= 1

    hypotheses = await mem.list_hypotheses("barren")
    assert len(hypotheses) >= 1
    assert hypotheses[0].result == "open"


async def test_specialist_sandbox_exec_reproduced_bypasses_format_check(tmp_path: Path):
    """Gate: sandbox_exec-verified candidate accepted even without CTF{} format."""
    art = tmp_path / "binary"
    art.write_bytes(b"\x7fELF" + b"\x00" * 32)

    class SandboxVerifiedEngine:
        category = Category.reverse
        async def solve(self, task):
            return EngineResult(
                candidate="correct_password_not_ctf_format",
                evidence=["binary accepted with exit 0"],
                reproduced=True,
                reproduction={"method": "sandbox_exec", "artifact": str(art), "expect_exit": 0},
                technique=["direct-compare-xor"],
            )

    bus = InMemoryBus()
    mem = InMemoryWorkingMemory()
    agent = SpecialistAgent(Category.reverse, bus, mem, None, Researcher(),
                            engine=SandboxVerifiedEngine())
    gate = Gate(bus, mem)

    sub = bus.subscribe("ctf.candidates", group="test")
    read = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)

    await agent.handle(Task(
        challenge_id="sandbox-bypass", category=Category.reverse,
        artifacts=[str(art)], flag_format=r"CTF\{[^}]+\}",
    ))

    raw = await asyncio.wait_for(read, 1)
    cand = Candidate.model_validate(raw)
    verdict = await gate.evaluate(cand)
    assert verdict.status == "solved"


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
    test_biobrain_adapter_passes_resolved_artifact_context_to_pipeline,
    test_reverse_tools_fake_elf_extracts_embedded_strings,
    test_reverse_tools_handles_missing_readelf_and_objdump_gracefully,
    test_static_detail_fake_objdump_output_is_capped,
    test_static_detail_classifies_imports_and_anchors,
    test_reverse_decision_success_failure_strings_trigger_follow_string_references,
    test_reverse_decision_compare_imports_trigger_string_reference_analysis,
    test_reverse_decision_stripped_and_missing_symtab_trigger_disassembly_summary,
    test_reverse_decision_no_matching_rules_returns_empty_result,
    test_reverse_decision_refines_from_compare_import_facts,
    test_reverse_decision_refines_from_input_import_facts,
    test_reverse_decision_refines_from_rodata_ascii_hints,
    test_reverse_decision_refines_from_missing_symtab,
    test_follow_string_references_returns_anchors_for_fake_elf,
    test_follow_string_references_missing_objdump_and_readelf_degrades_cleanly,
    test_extract_check_path_finds_compare_call,
    test_extract_check_path_infers_compare_call_without_symbol_facts,
    test_extract_check_path_keeps_helper_window_before_compare,
    test_extract_check_path_keeps_plt_offset_helper_window,
    test_extract_transform_path_plt_offset_label_is_not_filtered,
    test_extract_check_path_finds_branch_window,
    test_extract_check_path_includes_rodata_hints,
    test_extract_check_path_missing_symbols_degrades_cleanly,
    test_extract_check_path_does_not_execute_or_read_artifact,
    test_extract_transform_path_finds_helper_and_xor_loop,
    test_extract_transform_path_degrades_cleanly_without_helper,
    test_reverse_deterministic_self_xor_solver_recovers_candidate,
    test_reverse_tool_missing_degrades_cleanly,
    test_reverse_tool_runner_uses_no_shell,
    test_reverse_tool_never_executes_artifact_directly,
    test_reverse_tool_output_is_capped,
    test_reverse_tool_result_extracts_summary_lines,
    test_reverse_tool_result_extracts_structured_facts,
    test_reverse_tool_formatter_includes_summary_block,
    test_reverse_action_mapping_for_follow_string_references,
    test_biobrain_adapter_emits_reverse_preanalysis_trace,
    test_reverse_tools_do_not_execute_artifact,
    test_reverse_registry_tools_do_not_execute_artifact,
    test_biobrain_prompt_contains_reverse_decision_result,
    test_biobrain_adapter_short_circuits_on_deterministic_reverse_candidate,
    test_specialist_barren_loop_emits_hypothesis_and_no_candidate,
    test_specialist_sandbox_exec_reproduced_bypasses_format_check,
    test_crypto_engine_xor_brute_force_recovers_flag,
    test_crypto_engine_caesar_recovers_flag,
    test_forensics_engine_finds_flag_in_strings,
    test_crypto_decision_rsa_fields_trigger_rsa_analysis,
    test_stego_decision_png_triggers_lsb_scan,
]

if __name__ == "__main__":
    import tempfile
    for t in TESTS:
        with tempfile.TemporaryDirectory() as d:
            asyncio.run(t(Path(d)))
        print(f"PASS {t.__name__}")
