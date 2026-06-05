# TODO — CTF Skill Pack Runtime

This file tracks the remaining work after the current P0/P1 stabilization pass. The project currently implements a CTF-oriented agent framework: challenge ingestion, local triage, category routing, specialist workers, candidate generation, evidence-aware Gate validation, sandbox boundaries, and local smoke tests.

## Current baseline

Implemented and smoke-tested:

- [x] Skill pack structure with foundation and category `SKILL.md` files.
- [x] Runtime contracts in `runtime/ctfrt/contracts.py` aligned with `shared/schemas.md`.
- [x] Gate validation hardened:
  - [x] Reject invalid flag format when no oracle exists.
  - [x] Reject unverified candidates.
  - [x] Reject empty evidence ledger.
  - [x] Reject patched-binary success without oracle acceptance.
  - [x] Reject `oracle_validation="failed"` even if local validation passed.
  - [x] Separate proof tiers with `validation_level`.
- [x] Binary-safe sandbox message serialization using base64 JSON encoding for byte fields.
- [x] In-memory working memory for local development without Redis.
- [x] Redis-backed working memory still available for distributed mode.
- [x] In-memory bus improved:
  - [x] Per-topic backlog replay for first late subscriber.
  - [x] Fan-out across groups.
  - [x] Round-robin balancing within the same group.
- [x] Category-specific task topics, for example `ctf.tasks.reverse` and `ctf.tasks.misc`.
- [x] Deterministic `SKILL.md` path resolution independent of current working directory.
- [x] Minimal specialist path:
  - [x] Static artifact scan.
  - [x] Deterministic embedded flag extraction.
  - [x] Candidate emission.
  - [x] Gate-controlled final verdict.
- [x] Local CLI:
  - [x] `solve-local` for in-process smoke solving.
  - [x] `submit` for Kafka/distributed mode.
  - [x] `show-trace` and `export-trace`.
  - [x] `solve-local --timeout`.
- [x] Sandbox hardening:
  - [x] Reject absolute artifact paths.
  - [x] Reject `../` traversal.
  - [x] Return a `SandboxResult` instead of crashing on Docker launch errors.
- [x] Local triage based on magic bytes/extensions for ELF, PE, Mach-O, images, archives, PCAP, scripts, text/log files, HTTP hints, and crypto hints.
- [x] Smoke test suite in `runtime/tests/smoke_runtime.py`.
- [x] Append-only local trace persistence to `.ctfrt/traces`.
- [x] `solve-local` subprocess no-hang behavior covered by regression tests.
- [x] `solve-local` per-run `run_id` tagging for local traces.
- [x] Trace filtering support:
  - [x] `show-trace --latest`
  - [x] `show-trace --run-id <id>`
  - [x] `export-trace --latest`
  - [x] `export-trace --run-id <id>`
- [x] Engine-backed local reverse solve path:
  - [x] `BioBrainAdapter` dispatch from `solve-local`.
  - [x] Artifact-first XOR JSON solve for local `xor-crackme`.
  - [x] Gate acceptance with `technique=xor,keygen-inversion`.
- [x] Packaging hygiene:
  - [x] Canonical runtime location is `runtime/ctfrt/`.
  - [x] Exclude `__pycache__` and `.pyc` from release ZIPs.
  - [x] Avoid duplicated top-level runtime files.

## Current milestone

Active milestone: full integration — closing the live memory/intelligence wiring gaps.

Validated recently (P0→P3 infrastructure pass):

- [x] `make llm-drive LLM_ARTIFACT=/tmp/ctf-real/selfkey/selfkey ...` → `solved technique=direct-compare-xor` (real stripped binary).
- [x] Deterministic self-XOR solver + PLT-offset fix for stripped binaries.
- [x] Bounded specialist step loop with hypothesis ledger (P1.4).
- [x] pytest framework, shared fixtures, 6 full-stack integration tests (P2.1-P2.3).
- [x] Crypto/forensics/stego decision trees and deterministic engines (P3.1).
- [x] Handoff depth guard + board state CLI command (P3.2/P3.3).
- [x] LTM factory, CMS consolidate(), agency registry lazy adapters, DeepSearcher gap-filling loop.
- [x] Technique corpus in `vendor/techniques/` (9 categories, When/Tools/Caveats).
- [x] GitHub Actions CI (Python 3.11/3.12, uv, compile + pytest).

Next recommended work:

- [ ] Wire `ltm.consolidate()` in `Orchestrator.on_flag()` (post-solve lesson recording).
- [ ] Wire LTM lessons into `SpecialistAgent` at task intake (retrieve → enrich reasoning).
- [ ] Implement `integrations/agentic_rag/` and `integrations/enhanced_deep_search/` adapters.
- [ ] Add corpus references to all SKILL.md files.
- [ ] Add regression ensuring every non-static engine path ends in a terminal event.

## P0 — Must fix before serious use

These items affect correctness, isolation, or the ability to trust runtime output.

### P0.1 — Add structured runtime logging

Status: completed.

Problem: distributed mode is too quiet. If Kafka/Redis routing fails, debugging is harder than necessary.

Tasks:

- [x] Add consistent structured logs to `run.py`, `orchestrator.py`, `agent.py`, `gate.py`, `sandbox.py`, and `cli.py`.
- [x] Log component startup/shutdown.
- [x] Log topic subscriptions and publications at debug level.
- [x] Log candidate verdicts with challenge ID, candidate ID, status, validation level, and rejection reasons.
- [x] Add `CTF_LOG_LEVEL` environment variable.
- [x] Keep flag values redacted by default unless `CTF_DEBUG_FLAGS=1` is set.

Acceptance criteria:

- [x] Running `python -m ctfrt.cli solve-local ...` shows a clear high-level event trail.
- [x] Running Kafka mode shows which component consumed and published each major message.
- [x] Logs do not leak flags by default.

### P0.2 — Define a safe artifact workspace model

Status: completed.

Problem: artifacts are currently passed as paths. Path traversal is blocked at sandbox execution, but the broader challenge workspace model is still loose.

Tasks:

- [x] Add an explicit `Challenge.workdir` or `Artifact` contract.
- [x] Store artifacts relative to the challenge workspace.
- [x] Ensure every artifact path resolves under the challenge workspace.
- [x] Reject symlink escape from the workspace.
- [x] Normalize paths once at challenge ingestion.
- [x] Update CLI to copy or register artifacts into a challenge workspace.

Acceptance criteria:

- [x] No runtime component needs to trust arbitrary absolute paths from a message.
- [x] Path traversal and symlink traversal are rejected before sandbox or specialist use.

### P0.3 — Improve sandbox execution policy

Status: completed.

Problem: sandbox hardening exists, but the execution policy is still minimal.

Tasks:

- [x] Add explicit Docker image configuration, for example `CTF_SANDBOX_IMAGE`.
- [x] Run containers with read-only root filesystem by default.
- [x] Add memory, CPU, process, and file-size limits.
- [x] Disable network by default and enforce network opt-in.
- [x] Mount only the challenge workspace.
- [x] Drop Linux capabilities.
- [x] Add seccomp/AppArmor profile support where available.
- [x] Add timeout kill and cleanup guarantees.

Acceptance criteria:

- [x] Untrusted binaries never execute directly on the host.
- [x] A timed-out process is killed and the container is removed.
- [x] Network access is impossible unless explicitly requested.

### P0.4 — Add flag redaction and secret-handling policy

Status: completed.

Problem: traces, logs, and memory can contain flags. This is useful for CTF solving but bad for shared logs or screenshots.

Tasks:

- [x] Add `redact_flag()` helper.
- [x] Redact candidate strings in logs by default.
- [x] Redact candidate strings in trace payloads unless explicitly allowed.
- [x] Keep full flag only in candidate records and final result path.
- [x] Document how to enable full debug output locally.

Acceptance criteria:

- [x] Normal logs do not expose `CTF{...}` values.
- [x] Smoke tests still verify the final flag value internally.

## P1 — Next quality improvements

These items improve developer experience, routing quality, and reproducibility.

### P1.1 — Runtime startup/shutdown hardening

Status: completed.

Tasks:

- [x] Make all long-running component tasks cancel cleanly.
- [x] Ensure `bus.stop()` is always called.
- [x] Ensure Redis/Kafka clients close cleanly.
- [x] Avoid leaked async tasks in `solve-local` subprocess path.
- [x] Handle `Ctrl+C` predictably.
- [x] Add smoke test for cancellation/shutdown.

Acceptance criteria:

- [x] Repeated `solve-local` subprocess runs do not hang on shutdown.
- [x] `Ctrl+C` exits cleanly without stack traces in normal operation.

### P1.2 — Improve CLI usability

Status: completed.

Tasks:

- [x] Add `ctfrt.cli init-workdir`.
- [x] Add `ctfrt.cli inspect` to print triage and routing decision without solving.
- [x] Add `ctfrt.cli validate-candidate` for Gate-only testing.
- [x] Add `--json` output mode.
- [x] Add `--timeout` to `solve-local`.
- [x] Add trace filtering to `show-trace` and `export-trace`.
  - [x] `--latest`
  - [x] `--run-id`
- [x] Add `summarize-trace`.
- [x] Add `validate-trace`.
- [x] Add better exit codes:
  - [x] `0` solved.
  - [x] `1` not solved.
  - [x] `2` runtime/config error.
  - [x] `3` unsafe input rejected.

Acceptance criteria:

- [x] A user can diagnose triage/routing without reading code.
- [x] CLI output is scriptable in CI.

### P1.3 — Add Docker Compose for full local distributed mode

Status: completed.

Tasks:

- [x] Add or update `runtime/docker-compose.yml` for Kafka + Redis.
- [x] Add a `make distributed-up` or documented equivalent.
- [x] Add a distributed smoke test scenario.
- [x] Add topic list documentation.
- [x] Add troubleshooting section for Kafka advertised listeners.

Acceptance criteria:

- [x] User can start Kafka/Redis and run one submitted challenge end-to-end.
- [x] README includes exact commands and expected output.

### P1.4 — Add first real specialist tool loop

Status: substantially done (bounded loop + hypothesis ledger shipped; tool action schema deferred).

Tasks:

- [x] Add bounded specialist step loop (`_MAX_TOOL_STEPS=4`, `_MAX_BARREN=2`).
- [x] Add allowed tool registry per category (`reverse_tool_registry.py`, `crypto_tool_registry.py`).
- [x] Add hypothesis creation/update per step (upsert_hypothesis + bus publish).
- [x] Add stop condition after barren iterations.
- [x] Add handoff generation when classification changes.
- [ ] Add formal tool action schema (future: structured tool-call contract).
- [ ] Add read-only local tools as first-class agent actions (file, strings, entropy, archive listing).
- [ ] Add sandboxed execution as an explicit tool (currently engine-internal only).

Acceptance criteria:

- [x] A simple reverse/crackme artifact can be processed beyond static embedded-flag scan.
- [x] The agent emits a hypothesis ledger before `engine_no_candidate`.
- [ ] The agent emits a hypothesis with evidence for every candidate it proposes.

### P1.5 — Add researcher/deepsearcher backend stubs

Status: completed.

Tasks:

- [x] Implement local notes lookup backend.
- [x] Implement writeup corpus lookup backend.
- [x] Add advisory intelligence service contracts for future adapters.
- [x] Record `agentic-rag` as the future internal retrieval adapter.
- [x] Record `enhanced_deep_search` as the future external research adapter.
- [x] Keep researcher synchronous from the specialist perspective.
- [x] Add `ResearchResult` examples in tests.
- [x] Add deepsearcher escalation brief contract.

Acceptance criteria:

- [x] A specialist can ask a scoped question and receive structured evidence.
- [x] Advisory intelligence services have no execution authority and no Gate bypass.
- [x] `ctfrt` boots without `agentic-rag` or `enhanced_deep_search`.
- [x] Deepsearcher remains an escalation-only path when researcher cannot converge.

### P1.6 — Vendor or link technique corpus

Status: completed.

Tasks:

- [x] Add `vendor/techniques/` directory with When/Tools/Caveats entries for all 9 categories.
- [x] Add `vendor/corpus_index.yaml` mapping each category to its technique file.
- [ ] Update specialist SOPs to reference local corpus paths (in progress: SKILL.md files need `## Reference Corpus` section).

Acceptance criteria:

- [x] All 9 categories have local technique material in `vendor/techniques/`.
- [x] The runtime can run without live internet access.
- [ ] Each SKILL.md points at the corresponding `vendor/techniques/*.md` file.

## P2 — Testing and CI

### P2.1 — Convert smoke tests to pytest

Status: completed.

Tasks:

- [x] Add `pytest` test runner (`runtime/pytest.ini`, `asyncio_mode=auto`).
- [x] Keep `smoke_runtime.py` runnable directly (`if __name__ == "__main__"` preserved).
- [x] Add `runtime/tests/conftest.py` with fixtures for bus, memory, and 5 challenge artifact types.
- [x] Update `Makefile test` target to run `pytest runtime/tests/ -q`.
- [x] Add GitHub Actions CI (`.github/workflows/ci.yml`, Python 3.11/3.12).

### P2.2 — Add challenge fixtures

Status: completed.

Tasks:

- [x] Add safe local fixture factories in `conftest.py`:
  - [x] `embedded_flag_artifact` (text flag).
  - [x] `xor_crackme_artifact` (XOR JSON crackme).
  - [x] `fake_elf_strcmp_artifact` (fake ELF with strcmp pattern).
  - [x] `fake_png_artifact` (PNG magic + hidden text).
  - [x] `fake_pcap_artifact` (PCAP magic header).
- [x] No hostile binaries shipped (fixtures use safe magic bytes).

### P2.3 — Add integration tests

Status: completed.

Tasks:

- [x] Local end-to-end solve (`test_static_solve_full_loop`, `test_xor_solve_full_loop`).
- [x] No-solve timeout path (`test_no_solve_timeout`).
- [x] Wrong-format rejection path (`test_wrong_format_rejection`).
- [x] Handoff path (`test_handoff_path`).
- [x] Multi-challenge isolation (`test_multi_challenge_isolation`).
- [ ] Kafka/Redis distributed path (manual; documented in `make distributed-smoke`).

## P3 — Full CTF feature coverage

### P3.1 — Category specialist expansion

Status: substantially done (reverse, crypto, forensics, stego have decision trees + deterministic engines).

- [x] Reverse: decision tree, XOR/compare-extract solver, stripped binary support.
- [x] Crypto: decision tree + registry; XOR brute-force, Caesar brute-force, base64 decode.
- [x] Stego: decision tree with PNG/JPEG/WAV/text detection and LSB/metadata actions.
- [x] Forensics: decision tree with PCAP/disk/memory/log/archive kind detection.
- [ ] Web: request planning and local-only mode first.
- [ ] Binary-pwn: sandbox-only execution, no host execution.
- [ ] Jail-escape: prompt/filter modeling loop.
- [ ] OSINT: strict CTF-scope guardrails.
- [ ] Misc: recognize and route.

### P3.2 — Handoff behavior

Status: substantially done.

Tasks:

- [x] Implement orchestrator handoff consumption.
- [x] Add max handoff depth (`_MAX_HANDOFF_DEPTH=3`, `handoff_depth_exceeded` trace event).
- [x] Add `handoff_depth` field to `Handoff` contract (incremented on re-route).
- [x] Add trace of why a handoff happened (payload includes from/target/reason/depth).
- [ ] Preserve carry-over evidence and hypotheses across handoffs.

### P3.3 — Board state and multi-challenge orchestration

Status: partially done (board summary command shipped; per-category health and solve budget deferred).

Tasks:

- [x] Track challenge status (via trace JSONL files: routed, solved, engine_no_candidate, etc.).
- [x] Add `ctfrt.cli board` / `make board` summary command (id, status, category, elapsed, technique).
- [ ] Per-category worker health tracking.
- [ ] Add solve budget per challenge.
- [ ] Add explicit board model (queued/running/solved/failed/blocked states).

## P4 — Repository audit mode inspired by the Prepare → Scan → Validate → Prove → Patch pipeline

This is a separate operating mode, not part of the core CTF solver yet.

### P4.1 — Define repo-audit contracts

Status: design backlog.

New core objects:

- [ ] `RepositoryContext`
- [ ] `CodeMap`
- [ ] `ThreatModel`
- [ ] `Finding`
- [ ] `FindingValidation`
- [ ] `ProofRequest`
- [ ] `ProofResult`
- [ ] `PatchProposal`
- [ ] `PatchValidationResult`

Core invariant:

- [ ] No finding is confirmed without reproduction evidence.
- [ ] No patch is accepted without regression validation.
- [ ] No generated exploit or PoC runs outside the sandbox.

### P4.2 — Prepare stage

Status: design backlog.

Tasks:

- [ ] Ingest repository from Git or local path.
- [ ] Build repository map.
- [ ] Build call graph where language tooling supports it.
- [ ] Extract dependencies and lockfiles.
- [ ] Extract previous CVE/patch indicators from Git history.
- [ ] Generate threat model.

### P4.3 — Scan stage

Status: design backlog.

Tasks:

- [ ] Select 3–5 relevant subagents based on repo language and attack surface.
- [ ] Run per-function or per-component discovery.
- [ ] Emit candidate findings, not confirmed vulnerabilities.
- [ ] Assign evidence and confidence.

### P4.4 — Validate/dedup stage

Status: design backlog.

Tasks:

- [ ] Deduplicate findings by sink/source/root cause.
- [ ] Filter false positives.
- [ ] Add optional multi-model/persona debate.
- [ ] Require 2/3 agreement only as a signal, not as proof.
- [ ] Promote only findings with evidence to proof generation.

### P4.5 — Prove stage

Status: design backlog.

Tasks:

- [ ] Generate reproduction harness.
- [ ] Build Docker sandbox.
- [ ] Run self-contained proof.
- [ ] Add fuzzing stage where appropriate.
- [ ] Store proof artifacts and logs.

### P4.6 — Patch and patch validation stage

Status: design backlog.

Tasks:

- [ ] Generate minimal patch proposal.
- [ ] Run unit tests.
- [ ] Run regression tests.
- [ ] Replay proof to verify the vulnerability is fixed.
- [ ] Emit audit record.
- [ ] Keep autofix disabled until validation is reliable.

## Deferred / not now

These are useful but intentionally not on the immediate critical path.

- [ ] Model racing across multiple LLMs.
- [ ] Long-term self-improving skill consolidation.
- [ ] Benchmark harness against large public CTF corpora.
- [ ] Full VulnHub/boot2root kill-chain automation.
- [ ] Autonomous live web exploitation against shared platforms.
- [ ] Autofix merge automation.

## Manual test checklist

Run from `runtime/`:

```bash
PYTHONPATH=. python -m compileall -q ctfrt tests
PYTHONPATH=. python tests/smoke_runtime.py
```

Local end-to-end solve:

```bash
mkdir -p /tmp/ctf-test
echo 'hello noise CTF{static_win} end' > /tmp/ctf-test/note.txt
PYTHONPATH=. python -m ctfrt.cli solve-local \
  --name embedded-flag \
  --category misc \
  --artifact /tmp/ctf-test/note.txt \
  --flag-format 'CTF\{[^}]+\}'
```

Expected result:

```text
CTF{static_win}
```

## Release checklist

Before generating a new ZIP:

- [ ] Run smoke tests.
- [ ] Remove `__pycache__` and `.pyc` files.
- [ ] Check `PACKAGE_MANIFEST.txt`.
- [ ] Ensure only canonical runtime files exist under `runtime/ctfrt/`.
- [ ] Ensure `TODO.md` reflects current status.
- [ ] Zip from the directory containing `ctf-skill-pack/`, not from inside it.
