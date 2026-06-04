# Changelog

All notable changes to this repository should be recorded here.

The format is intentionally lightweight and milestone-oriented for local development.

## Unreleased

### Added

- `solve-local` trace usability improvements:
  - per-run `run_id` tagging for local trace events
  - `show-trace --latest`
  - `show-trace --run-id <id>`
  - `export-trace --latest`
  - `export-trace --run-id <id>`
- Subprocess regression tests for `python -m ctfrt.cli solve-local` covering:
  - clean solved exit
  - persisted trace output
  - unsolved timeout exit without subprocess hang
  - repeated-run trace filtering
- Engine/path regressions for:
  - BioBrain constructor argument wiring
  - bounded engine timeout behavior
  - artifact-first XOR solve before BioBrain retrieval

### Changed

- `solve-local` now records traces through an inline local sink instead of the background `TraceRecorder` path, avoiding `asyncio.run()` shutdown hangs in subprocess usage.
- `BioBrainAdapter` now supports local artifact-first reverse solving for XOR JSON fixtures before any memory/retrieval path.
- `solve-local` timeout failures now exit cleanly with a readable `not solved: timeout ...` message.

### Fixed

- Fixed the `solve-local` subprocess hang where a challenge could solve and print a flag but the CLI process remained alive during shutdown.
- Fixed the `needs_engine` dead-end in local engine runs by wiring the configured engine into `solve-local`.
- Fixed BioBrain adapter construction to pass the required `palace_path`-style kwargs expected by the current BioBrain runtime.
- Fixed `make llm-drive LLM_ARTIFACT=/tmp/ctf-test/xor_crackme.json ...` so the reverse XOR fixture now completes with a solved flag instead of stopping at `needs_engine`, `engine_error`, or `engine_no_candidate`.

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
