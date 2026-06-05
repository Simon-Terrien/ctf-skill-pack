# Changelog

All notable changes to this repository should be recorded here.

The format is intentionally lightweight and milestone-oriented for local development.

## Unreleased

### Added — Reverse engine

- `reverse_transform_path.py`: bounded static extractor for helper transform functions before compare checks (XOR/ADD/SUB loop detection, SIMD movdqa/movups pattern recognition, rodata load correlation, confidence scoring).
- `_solve_self_xor_compare()` in `engines.py`: deterministic self-referential XOR solver — reconstructs flag from objdump_rodata + objdump_disassembly without calling BioBrain.
- `_parse_rodata_bytes()` helper: extracts bytes from objdump hex dump output.
- PLT-offset filter fix: stripped binaries label helper calls as `<sym@plt+0xNNN>`. Changed filter from `"@plt"` to `"@plt>"` so offset references are not discarded as PLT stubs (`reverse_check_path.py`, `reverse_transform_path.py`).
- Disassembly output cap raised 4 KB → 16 KB so helper function bodies past the PLT section are captured.
- Gate: `sandbox_exec`-verified candidates bypass the flag-format check; binary accept is ground truth when no verifier is attached.
- Validated end-to-end on real stripped selfkey binary: `solved technique=direct-compare-xor`.

### Added — Agent bounded loop (P1.4)

- `SpecialistAgent` bounded step loop: up to `_MAX_TOOL_STEPS=4` engine iterations; early stop after `_MAX_BARREN=2` consecutive barren steps.
- Hypothesis ledger: each step emits a `Hypothesis` via `mem.upsert_hypothesis()` and publishes to `ctf.hypotheses`.
- `engine_no_candidate` trace now includes `hypothesis_count` and `steps`.
- `SpecialistAgent` constructor extended: `ltm=`, `intelligence_svc=`, `max_tool_steps=`.
- `ToolCallRecord` model and `ToolAction` Literal type added to `contracts.py`.
- Agent step loop emits `tool_call_record` trace event per engine dispatch with `duration_ms`.

### Added — Testing (P2.1–P2.3)

- pytest framework: `runtime/pytest.ini` (`asyncio_mode=auto`), `runtime/tests/conftest.py` (5 shared artifact fixtures, `_tmp_path` alias for standalone-runner compatibility).
- `runtime/tests/test_integration.py`: 7 full-stack tests wiring real Orchestrator + Gate + SpecialistAgent on InMemoryBus: static solve, XOR solve, no-solve timeout, wrong-format rejection, handoff, multi-challenge isolation, post-solve consolidate, terminal-event regression.
- `Makefile test` target now runs `pytest runtime/tests/ -q` (was smoke only).
- GitHub Actions CI: `.github/workflows/ci.yml` — Python 3.11/3.12, uv, compile + pytest on push/PR.

### Added — Category specialists (P3.1)

- `crypto_decision.py` + `crypto_tool_registry.py` + `CryptoEngine`: XOR brute-force (all 256 keys, flag-format match first), Caesar brute-force, base64 multi-layer decode, RSA field detection.
- `forensics_decision.py` + `ForensicsEngine`: PCAP/disk/memory/log/archive/binary kind detection; string search for embedded flags.
- `stego_decision.py`: PNG/JPEG/GIF/BMP/WAV/MP3/text detection; LSB, metadata, spectrogram, whitespace action rules.
- `web_decision.py` + `WebEngine`: URL/HTTP log/HTML detection; SQLi, SSTI, JWT, deserialization hints.
- `pwn_decision.py` + `PwnEngine`: ELF/PE/script detection; dangerous import classification; ROP/format-string hints.
- `jail_decision.py` + `JailEngine`: Python/bash/JavaScript jail type detection; subclass-enum, rbash-escape, proto-pollution actions.
- All six new engines wired into `engine_for_category()` under `CTF_AGENT_ENGINE=biobrain|deterministic`.

### Added — Orchestrator + gate (P3.2/P3.3)

- Handoff depth guard: `_MAX_HANDOFF_DEPTH=3`; `handoff_depth_exceeded` trace event when exceeded.
- `Handoff.handoff_depth` field incremented on each re-route.
- Handoff evidence carryover: on handoff emit, `Handoff.carry` populated with `evidence[:5]`, technique tags, open hypothesis IDs; receiving specialist includes carry in researcher question enrichment.
- `board_status` field on board dict: `running | solved | timed_out | failed`.
- `Orchestrator.on_trace()` updates `board_status` on terminal events (`engine_no_candidate → timed_out`, `engine_error → failed`).
- `Orchestrator.on_challenge()` initialises board with `board_status=running` and `started_at` timestamp.
- `ctfrt.cli board` subcommand: reads all `.ctfrt/traces/*.jsonl`, prints id/status/category/elapsed/technique table. `--json` flag for scripting.
- `make board` target.
- `solve-local --solve-budget N` flag: caps engine iterations via `SpecialistAgent.max_tool_steps`.

### Added — Integration seams

- `vendor/techniques/` (9 markdown files, When/Tools/Caveats for each category) + `vendor/corpus_index.yaml`.
- `## Reference Corpus` section added to all 9 category SKILL.md files.
- `BioBrainLongTermMemory` + `make_long_term_memory()` factory (`CTF_LTM_BACKEND=biobrain|none`).
- LTM wired into `Orchestrator` (retrieve at triage; results go into `task.triage["lessons"]`) and `SpecialistAgent` (prior lessons enrich researcher question).
- `Orchestrator.on_flag()` calls `ltm.consolidate()` after marking challenge solved.
- `MemoryConsumer.consolidate()` implemented in `cms_cag.py`: stores post-solve lesson in CMS L1/L2.
- `agency_registry.py` lazy-imports real adapters when env vars set; falls back to `NullIntelligenceService`.
- `integrations/agentic_rag/__init__.py`: `AgenticRagService` — keyword-overlap RAG over `vendor/techniques/*.md`, SKILL.md files, and solved trace summaries.
- `integrations/enhanced_deep_search/__init__.py`: `EnhancedDeepSearchService` — thin wrapper around `DeepSearcher`.
- `DeepSearcher.investigate()` gap-filling multi-hop loop: subsequent rounds re-query uncovered goal terms, deduplicate by source URL (replaces TODO stub).
- `SpecialistAgent` intelligence advisor: queries `intelligence_svc` at task intake; advisory answer's `recommended_next_action` appended to researcher question; emits `intelligence_advisory` trace.

### Added — Early trace/CLI work (previously in Unreleased)

- `solve-local` per-run `run_id` tagging; `show-trace --latest`; `show-trace --run-id`; `export-trace --latest/--run-id`.
- Subprocess regression tests covering: clean solved exit, persisted trace, unsolved timeout, repeated-run filtering.
- Advisory intelligence service contracts in `ctfrt.intelligence`; null-only registry in `ctfrt.agency_registry`.

### Changed

- `solve-local` records traces through an inline local sink (avoids asyncio shutdown hang).
- `BioBrainAdapter` performs local artifact-first reverse solving before any memory/retrieval path.
- `solve-local` timeout failures exit cleanly with `not solved: timeout ...`.
- `Makefile test` now delegates to pytest; `smoke` target preserved for standalone debugging.

### Fixed

- Stripped binary PLT-offset filter: `<sym@plt+0xNNN>` helper calls were incorrectly discarded as PLT stubs.
- `solve-local` subprocess hang on solved challenge.
- `needs_engine` dead-end in local engine runs.
- BioBrain constructor argument wiring.
- Lying-engine gate bypass: gate now tightens `sandbox_exec` format bypass to require `reproduction.method == "sandbox_exec"` (not any reproduced+local_passed candidate).

## v0.5.1-local-engine-solve-clean

Baseline local-engine milestone validated in the runtime:

- Ollama OpenAI-compatible provider path works
- `solve-local` works
- trace persistence works
- `show-trace` works
- `BioBrainAdapter` engine path works
- XOR non-plaintext local artifact solved
- Gate accepted candidate
- `technique=xor,keygen-inversion` recorded
- `.ctfrt` stays out of git
