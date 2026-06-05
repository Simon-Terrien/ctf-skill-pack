# CTF Skill Pack

A CTF agent framework with deterministic specialist engines, bounded reasoning loops, and an evidence-aware gate. Solves challenges locally (no external AI required for deterministic categories) and escalates to BioBrain for harder cases.

## Repository layout

```
runtime/ctfrt/         — agent runtime (orchestrator, gate, specialist agents, engines)
vendor/techniques/     — offline technique corpus (9 categories, When/Tools/Caveats)
integrations/          — optional intelligence adapters (agentic_rag, enhanced_deep_search)
{category}/SKILL.md   — per-category SOP + Reference Corpus link
shared/schemas.md      — wire contracts (read this first)
.github/workflows/     — GitHub Actions CI (Python 3.11/3.12)
```

## Architecture

```
Challenge
  │
  ▼
Orchestrator ── triage (magic bytes, LTM retrieve) ──► Task (per category)
  │                                                        │
  │                                                        ▼
  │                                               SpecialistAgent
  │                                                  │  │
  │                                     static scan ◄┘  │ engine dispatch
  │                                                      ▼ (bounded loop)
  │                                                  Engine
  │                                              (deterministic or BioBrain)
  │                                                      │
  │                                               Candidate
  │                                                      │
  └──────────────────────────────────────────────► Gate
                                                         │
                                                   solved / raw
```

### Key components

| Component | Description |
|-----------|-------------|
| `Orchestrator` | Triage, routing, board state, LTM retrieve at intake, handoff depth guard |
| `Gate` | Sole path to `solved`; independent reproduction verification; trusts `sandbox_exec` over flag format |
| `SpecialistAgent` | Bounded step loop (4 steps, 2-barren stop), hypothesis ledger, LTM lessons, advisory intelligence, handoff evidence carry |
| Decision modules | `{category}_decision.py` — signal detection → next-action inference |
| Engines | `BioBrainAdapter`, `CryptoEngine`, `ForensicsEngine`, `WebEngine`, `PwnEngine`, `JailEngine`, `StubReverseEngine` |
| Working memory | `InMemoryWorkingMemory` (dev) or `RedisWorkingMemory` (prod) |
| Long-term memory | `NullLongTermMemory` (default) or `BioBrainLongTermMemory` (`CTF_LTM_BACKEND=biobrain`) |
| Intelligence | `NullIntelligenceService` (default); optional `AgenticRagService` / `EnhancedDeepSearchService` |

### Specialist categories

All 9 categories are routed: `reverse`, `crypto-attack`, `web-exploit`, `binary-pwn`, `forensics`, `stego`, `jail-escape`, `osint`, `misc`.

Decision trees + deterministic engines: reverse, crypto, forensics, stego, web, pwn, jail.
BioBrain escalation: all categories via `CTF_AGENT_ENGINE=biobrain`.

## Quick start

```bash
# Install dependencies (uv required)
uv sync

# Solve a static text challenge
make solve-local ARGS="--name my-flag --category misc \
  --artifact /tmp/note.txt --flag-format 'CTF\{[^}]+\}'"

# Solve a real binary (needs CTF_AGENT_ENGINE=biobrain for full analysis)
make llm-drive LLM_ARTIFACT=/path/to/binary LLM_NAME=my-challenge LLM_CATEGORY=reverse

# Show challenge board
make board

# Run all tests
make test
```

## CLI reference

```
ctfrt <command> [options]
```

| Command | Description |
|---------|-------------|
| `solve-local` | In-process solve. Key flags: `--timeout`, `--solve-budget N`, `--flag-format`, `--json` |
| `board` | Challenge status table (id, status, category, elapsed, technique). `--json` for scripting |
| `init-workdir` | Register artifacts into challenge workspace |
| `inspect` | Triage + routing decision without solving |
| `validate-candidate` | Gate-only candidate validation |
| `submit` | Submit challenge to distributed Kafka bus |
| `show-trace` | Display trace events. Flags: `--latest`, `--run-id <id>`, `--json` |
| `summarize-trace` | Print trace summary |
| `export-trace` | Export trace to file |
| `validate-trace` | Validate trace state machine |

## Make targets

```bash
make test               # pytest runtime/tests/ -q  (121 tests)
make smoke              # standalone smoke_runtime.py
make compile            # byte-compile check
make board              # challenge status table
make solve-local        # ARGS="..." — in-process solve
make llm-drive          # LLM_ARTIFACT=... LLM_NAME=... LLM_CATEGORY=...
make distributed-up     # start Kafka + Redis via Docker Compose
make distributed-down   # stop Kafka + Redis
make trace-show         # CHALLENGE_ID=... — show trace
make trace-export       # CHALLENGE_ID=... — export trace
make board              # challenge status table
```

## Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `CTF_AGENT_ENGINE` | Engine mode: `biobrain`, `deterministic`, or empty | — |
| `CTF_LTM_BACKEND` | Long-term memory: `biobrain` or `none` | `none` |
| `CTF_INTELLIGENCE_INTERNAL` | Internal advisor: `agentic_rag` | — |
| `CTF_INTELLIGENCE_EXTERNAL` | External advisor: `enhanced_deep_search` | — |
| `CTF_MEMORY_QUERY` | CMS memory: `cms` or `none` | `none` |
| `CTF_KAFKA` | Kafka bootstrap server | `localhost:9092` |
| `CTF_REDIS` | Redis URL | `redis://localhost:6379/0` |
| `CTF_CHALLENGE_ROOT` | Artifact workspace root | `/tmp/ctf` |
| `CTF_TRACE_DIR` | Local trace storage | `.ctfrt/traces` |
| `CTF_LOG_LEVEL` | Logging level | `INFO` |
| `CTF_DEBUG_FLAGS` | Set `1` to log flag values unredacted | — |

## Technique corpus

Offline technique references live in `vendor/techniques/` (one file per category, When/Tools/Caveats format). Each category's `SKILL.md` points at the corresponding file. The corpus is consulted by the `AgenticRagService` internal intelligence adapter when `CTF_INTELLIGENCE_INTERNAL=agentic_rag`.

## Two enforced invariants

1. No candidate is `solved` without `flag-discipline` (Gate approval).
2. No unknown artifact runs outside `exploit-sandbox`.

## Canonical package layout

The only canonical runtime implementation lives under `runtime/ctfrt/`. Do not ship duplicate files outside this directory.
