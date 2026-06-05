# Repository Guidelines

## Project Structure & Module Organization

The canonical runtime implementation lives under `runtime/ctfrt/`. Shared contracts live in `shared/schemas.md` — update that first. Each category has a top-level directory (`reverse/`, `web-exploit/`, `binary-pwn/`, …) anchored by `SKILL.md` which references the offline technique corpus at `vendor/techniques/{category}.md`. Optional intelligence adapters live under `integrations/`. Runtime tests live in `runtime/tests/`. Do not add or ship duplicate runtime files outside `runtime/ctfrt/`.

```
runtime/ctfrt/         — agent runtime (canonical)
vendor/techniques/     — offline corpus (When/Tools/Caveats per technique)
integrations/          — optional intelligence adapters
{category}/SKILL.md   — per-category SOP + Reference Corpus link
shared/schemas.md      — wire contracts
```

## Build, Test, and Development Commands

```bash
# Syntax check
make compile

# Full test suite (pytest, 121 tests)
make test

# Standalone smoke tests (no pytest dependency)
make smoke

# Single-process solve
make solve-local ARGS="--name foo --category reverse --artifact /tmp/foo.bin --flag-format 'CTF\{[^}]+\}'"

# Challenge status board
make board

# Real-binary end-to-end
make llm-drive LLM_ARTIFACT=/path/to/binary LLM_NAME=my-challenge LLM_CATEGORY=reverse

# Distributed runtime (requires Docker for Kafka/Redis)
make distributed-up
CTF_KAFKA=localhost:9092 CTF_REDIS=redis://localhost:6379/0 uv run python -m ctfrt.run

# Verbose pytest (from runtime/ directory)
uv run pytest runtime/tests/ -v
```

## Coding Style & Naming Conventions

Standard Python 3: 4-space indentation, `snake_case` functions/variables, `PascalCase` classes, short module names. Keep code direct and explicit; small orchestration layers over heavy abstraction. Prefer changing `shared/schemas.md` and canonical runtime code over copying logic into skill folders.

## Testing Guidelines

Add or update tests in `runtime/tests/` when changing runtime behavior. The test suite uses pytest with `asyncio_mode=auto` — no explicit `asyncio.run()` needed in test functions. Key test files:

- `smoke_runtime.py` — end-to-end smoke tests, also runnable standalone
- `test_engine_weld.py` — deterministic engine + reverse analysis unit tests
- `test_integration.py` — full-stack integration tests (Orchestrator + Gate + Agent)
- `test_cms_cag.py` — CMS memory integration
- `test_intelligence_contracts.py` — intelligence adapter contracts

Run `make test` before submitting any change. If a change affects CLI behavior, validate with `solve-local` on a tmp artifact.

## Agent Architecture

### SpecialistAgent constructor

```python
SpecialistAgent(
    category,          # Category enum
    bus,               # Bus (InMemoryBus or KafkaBus)
    mem,               # MemoryProtocol (InMemoryWorkingMemory or RedisWorkingMemory)
    llm,               # LLM | None
    researcher,        # Researcher
    engine=None,       # SolveEngine | None
    ltm=None,          # LongTermMemory | None  (default: NullLongTermMemory)
    intelligence_svc=None,  # IntelligenceService | None  (default: null)
    max_tool_steps=None,    # int | None  (default: _MAX_TOOL_STEPS=4)
)
```

### Bounded step loop

1. Static scan: search artifact text for flag regex. If found → emit Candidate → return.
2. If no engine → emit `needs_engine` trace → return.
3. Enrich researcher question with `task.triage["lessons"]` (prior LTM lessons) and `task.triage["carry"]` (handoff evidence).
4. If `intelligence_svc` set → query it → append `recommended_next_action` to researcher question → emit `intelligence_advisory` trace.
5. Loop up to `max_tool_steps` times:
   - Emit `engine_dispatch` trace.
   - Call `engine.solve(task)` → emit `tool_call_record` trace with `duration_ms`.
   - On handoff result → populate `Handoff.carry` with evidence + technique + open hypothesis IDs → publish Handoff → return.
   - Emit `Hypothesis` to working memory + `ctf.hypotheses` bus.
   - On candidate result → publish Candidate → emit `candidate_emitted` → return.
   - Barren step (no candidate, no handoff) → increment barren counter; if `barren >= _MAX_BARREN` → break.
6. Emit `engine_no_candidate` trace with `hypothesis_count` and `steps`.

### Decision modules

Each category has a `{category}_decision.py` following this pattern:

```python
@dataclass
class {Category}ArtifactSignals:
    ...

def analyze_{category}_artifact(data: bytes, filename: str = "") -> Signals:
    ...

class {Category}Decision(BaseModel):
    matched_rules: list[str]
    next_actions: list[str]
    inferred_techniques: list[str]
    confidence: float

def evaluate_{category}_decision(signals: Signals) -> Decision:
    ...
```

Available modules: `reverse_decision.py`, `crypto_decision.py`, `forensics_decision.py`, `stego_decision.py`, `web_decision.py`, `pwn_decision.py`, `jail_decision.py`.

### Intelligence & long-term memory

**Long-term memory (LTM):**
- Protocol: `retrieve(signals: list[str], k: int) → list[dict]`; `consolidate(challenge_id, lesson: dict)`
- Default: `NullLongTermMemory` (no-op). Enable BioBrain backend: `CTF_LTM_BACKEND=biobrain`
- Orchestrator calls `retrieve()` at triage; result goes into `task.triage["lessons"]`
- Orchestrator calls `consolidate()` after solve in `on_flag()`

**Advisory intelligence:**
- Protocol: `ask(IntelligenceQuestion) → IntelligenceAnswer`
- Default: `NullIntelligenceService`. Enable corpus RAG: `CTF_INTELLIGENCE_INTERNAL=agentic_rag`
- Enable external search: `CTF_INTELLIGENCE_EXTERNAL=enhanced_deep_search`
- Adapters live in `integrations/agentic_rag/` and `integrations/enhanced_deep_search/`
- Intelligence answers are advisory only — no Gate bypass, no execution authority

## Commit & Pull Request Guidelines

Use short, imperative commit messages: `fix runtime gate validation` or `add smoke test coverage`. Keep PRs focused on one logical change. Include a brief summary, the files changed, and the commands you ran. Note expected output if behavior changed.

## Security & Configuration Tips

Do not run unknown artifacts outside the sandbox layer. Do not mark a candidate solved without passing `flag-discipline`. Key environment variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `CTF_AGENT_ENGINE` | Engine mode (`biobrain`, `deterministic`, or empty) | — |
| `CTF_LTM_BACKEND` | Long-term memory backend (`biobrain`, `none`) | `none` |
| `CTF_INTELLIGENCE_INTERNAL` | Internal advisory adapter | — |
| `CTF_INTELLIGENCE_EXTERNAL` | External advisory adapter | — |
| `CTF_MEMORY_QUERY` | CMS memory mode (`cms`, `none`) | `none` |
| `CTF_KAFKA` | Kafka bootstrap server | `localhost:9092` |
| `CTF_REDIS` | Redis URL | `redis://localhost:6379/0` |
| `CTF_CHALLENGE_ROOT` | Artifact workspace root | `/tmp/ctf` |
| `CTF_LOG_LEVEL` | Logging level | `INFO` |
| `CTF_DEBUG_FLAGS` | Set to `1` to log flag values unredacted | — |
