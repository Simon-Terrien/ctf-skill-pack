# BioBrain

**A modular agentic runtime for orchestrating specialized local models, tools, memory, and safety gates.**

BioBrain is a control-plane kernel for agentic systems. It routes tasks to specialized LLMs by role (planner, coder, critic, security reviewer), enforces safety gates before tool execution, maintains structured memory across sessions, and produces full audit traces for every decision.

It is **not** an autonomous agent. It is the runtime kernel that agents run on.

## Architecture

```
User Goal
   ↓
Intent / Risk Classification
   ↓
Reflex Safety Gate (block / sanitize / escalate / route)
   ↓
Memory Recall (working / episodic / semantic / procedural)
   ↓
Executive Decision (reasoning mode + action plan + inhibition)
   ↓
Model Router
   ├── Planner    → qwen3.6          (goal decomposition)
   ├── Coder      → qwen3-coder      (code generation)
   ├── Critic     → qwen36-a3b       (review / contradiction)
   ├── Reasoning  → zaya1-8b         (deep chain-of-thought)
   ├── Security   → qwen3.6          (OWASP-aware review)
   └── Fast       → qwen3.5:4b       (classification, simple tasks)
   ↓
Tool Execution (sandboxed: shell, git, pytest, file ops, code search)
   ↓
Feedback Verification
   ↓
Memory Update + Audit Trace
```

## Modules

| Module | Role |
|--------|------|
| `ingest/` | Source-aware input with trust tagging |
| `perception/` | Intent, entity, risk, operation class extraction |
| `attention/` | Priority, risk score, confidence, reasoning suggestion |
| `safety/` | Deterministic reflex gates (block/sanitize/escalate/route) |
| `memory/` | Four memory types over MemPalace (working/episodic/semantic/procedural) |
| `identity/` | Structured policy (allowed domains, forbidden ops, approval rules) |
| `executive/` | Strategy selection, inhibition, action planning |
| `cognition/` | Pluggable reasoning with model router |
| `action/` | Tool execution with guards, timeout, dry-run, schema validation |
| `feedback/` | Validation, error correction, learning |
| `modulation/` | Global mode (normal/risk/audit/incident) affecting all modules |
| `agents/` | Specialized agents: planner, coder, critic, security reviewer |
| `domain/` | Pentest tools, dev tools, OWASP playbooks, CyberRange runner |
| `ops/` | Health monitor, structured tracing, audit logger, trace export |
| `runtime/` | Pipeline, session, orchestrator (plan/act/observe/replan) |

## Install

```bash
pip install -e .
```

## Quick Start

```python
from biobrain.runtime import BioBrain, Session, Orchestrator
from biobrain.core.enums import InputSource, SystemMode
from biobrain.cognition.router import ModelRouter
from biobrain.domain import register_dev_tools, register_pentest_tools

# Create brain with multi-model routing
brain = BioBrain(palace_path="~/.mempalace/palace")

# Register tools
register_dev_tools(sandbox_root="./project")
register_pentest_tools()

# Single-pass processing
trace = brain.process("scan the auth endpoint", source=InputSource.USER)
print(trace.audit_summary)

# Multi-turn session
session = Session(brain, wing="wing_adeo")
session.turn("analyze the authentication flow")
session.turn("check for IDOR on user endpoints")
session.set_mode(SystemMode.AUDIT, "generating report")
session.turn("generate findings report")
print(session.summary)

# Autonomous orchestration
orch = Orchestrator(brain, max_steps=10, halt_on_escalation=True)
result = orch.run("scan auth, check tokens, generate report")
print(result.summary)
```

See [USAGE.md](USAGE.md) for detailed examples.

## Tests

```bash
pytest -v   # 229 tests
```

## Configuration

```yaml
# biobrain.yaml
palace_path: "~/.mempalace/palace"
playbook_dir: "./configs/playbooks"
identity_config: "./configs/identity_phantom.yaml"

models:
  planner:   {model: "qwen3.6", temperature: 0.3}
  coder:     {model: "qwen3-coder", temperature: 0.2}
  critic:    {model: "qwen36-a3b-q4km", temperature: 0.4}
  reasoning: {model: "zaya1-8b", temperature: 0.3}
  fast:      {model: "qwen3.5:4b", max_tokens: 512}
  security:  {model: "qwen3.6", temperature: 0.3}

audit_log: "./audit.jsonl"
initial_mode: "normal"
```

Environment overrides: `BIOBRAIN_PALACE_PATH`, `BIOBRAIN_LLM_MODEL`, etc.

## CLI

```bash
biobrain run "scan the auth endpoint" --mode audit --json
biobrain session --wing wing_adeo
biobrain orchestrate "scan auth, check tokens, report" --max-steps 5
biobrain benchmark --suite coding --models qwen3-coder,qwen3.6
biobrain status
```

## Project Status

- **Architecture:** complete — 15 modules, typed signal contracts, full audit trail
- **Safety:** enforced — reflex gates, structured policy, confirmation blocking, sandbox
- **Cognition:** pluggable — model router with role-based assignment, 7 reasoning modes
- **Tools:** sandboxed — shell, git, pytest, file ops, code search, pentest tools
- **Observability:** full — event bus, structured tracing, JSONL audit, health metrics
- **Domain:** OWASP playbooks, CyberRange exercise runner, benchmark harness

## License

Apache-2.0
