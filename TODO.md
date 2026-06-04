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

Active milestone: trace usability and local engine reliability.

Validated recently:

- [x] `make llm-drive LLM_ARTIFACT=/tmp/ctf-test/xor_crackme.json ...` reaches `engine_dispatch`.
- [x] The local `biobrain` engine path emits `candidate_emitted`.
- [x] Gate emits `candidate_accepted`.
- [x] Final trace records `solved technique=xor,keygen-inversion`.
- [x] Missing MemPalace is warning-only for artifact-first XOR solving.

Next recommended work:

- [x] Add `summarize-trace`.
- [x] Add `validate-trace`.
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

Status: partially done.

Tasks:

- [ ] Make all long-running component tasks cancel cleanly.
- [ ] Ensure `bus.stop()` is always called.
- [ ] Ensure Redis/Kafka clients close cleanly.
- [x] Avoid leaked async tasks in `solve-local` subprocess path.
- [ ] Handle `Ctrl+C` predictably.
- [ ] Add smoke test for cancellation/shutdown.

Acceptance criteria:

- [x] Repeated `solve-local` subprocess runs do not hang on shutdown.
- [ ] `Ctrl+C` exits cleanly without stack traces in normal operation.

### P1.2 — Improve CLI usability

Status: in progress.

Tasks:

- [ ] Add `ctfrt.cli init-workdir`.
- [ ] Add `ctfrt.cli inspect` to print triage and routing decision without solving.
- [ ] Add `ctfrt.cli validate-candidate` for Gate-only testing.
- [ ] Add `--json` output mode.
- [x] Add `--timeout` to `solve-local`.
- [x] Add trace filtering to `show-trace` and `export-trace`.
  - [x] `--latest`
  - [x] `--run-id`
- [x] Add `summarize-trace`.
- [x] Add `validate-trace`.
- [ ] Add better exit codes:
  - [ ] `0` solved.
  - [ ] `1` not solved.
  - [ ] `2` runtime/config error.
  - [ ] `3` unsafe input rejected.

Acceptance criteria:

- [ ] A user can diagnose triage/routing without reading code.
- [ ] CLI output is scriptable in CI.

### P1.3 — Add Docker Compose for full local distributed mode

Status: open.

Tasks:

- [x] Add or update `runtime/docker-compose.yml` for Kafka + Redis.
- [ ] Add a `make distributed-up` or documented equivalent.
- [ ] Add a distributed smoke test scenario.
- [x] Add topic list documentation.
- [ ] Add troubleshooting section for Kafka advertised listeners.

Acceptance criteria:

- [ ] User can start Kafka/Redis and run one submitted challenge end-to-end.
- [x] README includes exact commands and expected output.

### P1.4 — Add first real specialist tool loop

Status: partially done.

Problem: specialists currently have deterministic static scan plus a bounded engine path, but category-specific tool loops remain thin.

Recommended first target: `reverse`.

Tasks:

- [ ] Add bounded specialist step loop.
- [ ] Add tool action schema.
- [ ] Add allowed tool registry per category.
- [ ] Add read-only local tools first:
  - [ ] `file` equivalent.
  - [ ] magic byte inspection.
  - [ ] strings extraction.
  - [ ] entropy check.
  - [ ] archive listing.
- [ ] Add sandboxed execution as an explicit tool.
- [ ] Add hypothesis creation/update per step.
- [ ] Add stop condition after barren iterations.
- [ ] Add handoff generation when classification changes.

Acceptance criteria:

- [x] A simple reverse/crackme artifact can be processed beyond static embedded-flag scan.
- [ ] The agent emits a hypothesis ledger and a candidate only with evidence.

### P1.5 — Add researcher/deepsearcher backend stubs

Status: open.

Tasks:

- [ ] Implement local notes lookup backend.
- [ ] Implement writeup corpus lookup backend.
- [ ] Implement optional web-backed researcher adapter.
- [x] Keep researcher synchronous from the specialist perspective.
- [ ] Add `ResearchResult` examples in tests.
- [ ] Add deepsearcher escalation brief contract.

Acceptance criteria:

- [ ] A specialist can ask a scoped question and receive structured evidence.
- [ ] Deepsearcher is only used when researcher cannot converge.

### P1.6 — Vendor or link technique corpus

Status: open.

Context: the skill SOPs are thin decision loops. Technique depth should come from a vendored corpus or explicit local reference library.

Tasks:

- [ ] Decide whether to vendor all categories or only the categories in active use.
- [ ] Add `vendor/` directory or `references/` directory.
- [ ] Add source attribution and license notes.
- [ ] Add corpus index file for category-to-reference mapping.
- [ ] Update specialist SOPs to reference local corpus paths.

Acceptance criteria:

- [ ] Reverse, crypto, web, pwn, forensics, stego, OSINT, jail, and misc SOPs can point to local technique material.
- [ ] The runtime can run without live internet access.

## P2 — Testing and CI

### P2.1 — Convert smoke tests to pytest

Status: open.

Tasks:

- [ ] Add `pytest` test runner.
- [ ] Keep `smoke_runtime.py` runnable directly for zero-friction debugging.
- [ ] Add fixtures for bus, memory, gate, orchestrator, and specialist.
- [ ] Add CI command documentation.

### P2.2 — Add challenge fixtures

Status: open.

Tasks:

- [ ] Add safe local fixture files:
  - [ ] embedded text flag.
  - [ ] fake ELF.
  - [ ] fake PNG with metadata-like text.
  - [ ] basic crypto text challenge.
- [ ] Add optional compiled toy crackme fixture generation script.
- [ ] Avoid shipping hostile binaries by default.

### P2.3 — Add integration tests

Status: open.

Tasks:

- [ ] Local end-to-end solve.
- [ ] No-solve timeout path.
- [ ] Wrong-format rejection path.
- [ ] Handoff path.
- [ ] Sandbox request/result path.
- [ ] Kafka/Redis distributed path, optional or marked slow.

## P3 — Full CTF feature coverage

### P3.1 — Category specialist expansion

Status: open.

Prioritized order:

- [ ] Reverse: strings, file info, simple compare extraction, sandboxed run.
- [ ] Crypto: detect primitive, run safe local scripts, emit reproduction.
- [ ] Stego: metadata, strings, binwalk-like carving, LSB/spectrogram hooks.
- [ ] Forensics: PCAP/log/image triage, timeline extraction hooks.
- [ ] Web: request planning and local-only mode first; avoid live shared targets by default.
- [ ] Binary-pwn: sandbox-only execution, no host execution.
- [ ] Jail-escape: prompt/filter modeling loop.
- [ ] OSINT: strict CTF-scope guardrails.
- [ ] Misc: recognize and route.

### P3.2 — Handoff behavior

Status: open.

Tasks:

- [x] Implement orchestrator handoff consumption.
- [ ] Deduplicate repeated handoffs.
- [ ] Preserve carry-over evidence and hypotheses.
- [ ] Add max handoff depth.
- [ ] Add trace of why a handoff happened.

### P3.3 — Board state and multi-challenge orchestration

Status: open.

Tasks:

- [ ] Add board model.
- [ ] Track challenge status: queued, running, solved, failed, blocked.
- [ ] Track per-category worker health.
- [ ] Add board summary command.
- [ ] Add solve budget per challenge.

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
