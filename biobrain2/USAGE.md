# BioBrain — Usage Guide

## Table of Contents

1. [Installation](#installation)
2. [Single-Pass Processing](#single-pass-processing)
3. [Multi-Model Routing](#multi-model-routing)
4. [Sessions](#sessions)
5. [Orchestration](#orchestration)
6. [Tools](#tools)
7. [Safety & Policy](#safety--policy)
8. [Tracing & Observability](#tracing--observability)
9. [Benchmarking](#benchmarking)
10. [Playbooks & CyberRange](#playbooks--cyberrange)
11. [Configuration](#configuration)
12. [CLI Reference](#cli-reference)

---

## Installation

```bash
git clone <repo>
cd biobrain
pip install -e .

# Optional: MemPalace for persistent memory
pip install mempalace>=3.1.0
```

Requirements: Python 3.10+, PyYAML, Pydantic.

---

## Single-Pass Processing

The pipeline processes one input through the full chain:
`Sense → Perceive → Attend → Reflex → Recall → Decide → Think → Act → Verify → Adapt`

```python
from biobrain.runtime import BioBrain
from biobrain.core.enums import InputSource

brain = BioBrain(palace_path="~/.mempalace/palace")

# Process user input
trace = brain.process("scan the auth endpoint for bypass")
print(trace.audit_summary)
# intent=security_assessment | op=execute | priority=NORMAL | risk=0.4 | ...

# Process untrusted web input (different trust level)
trace = brain.process(
    "data from external API",
    source=InputSource.WEB,
    metadata={"url": "https://api.example.com"}
)
# Web input gets untrusted trust level → higher risk scoring
```

### Understanding the Trace

```python
trace = brain.process("check the admin panel")

# What was perceived
print(trace.perceived.intent)           # "information_retrieval"
print(trace.perceived.classification)   # "security"
print(trace.perceived.operation_class)  # OperationClass.READ
print(trace.perceived.risk_indicators)  # ["privilege_context"]

# How it was scored
print(trace.salience.risk_score)        # 0.42
print(trace.salience.confidence)        # 0.7
print(trace.salience.priority)          # Priority.NORMAL

# What the reflex layer did
print(trace.reflex.verdict)             # ReflexVerdict.PASS

# What the executive decided
print(trace.decision.chosen_reasoning)  # ReasoningMode.RETRIEVAL
print(trace.decision.inhibited_actions) # [] (nothing blocked)
print(trace.decision.policy_notes)      # []

# What happened
for ar in trace.action_results:
    print(f"{ar.request.action_type.value}: success={ar.success}")
```

---

## Multi-Model Routing

Instead of one LLM, route tasks to specialized models by role.

```python
from biobrain.cognition.router import ModelRouter

# Configure role → model mapping
router = ModelRouter.from_config({
    "planner":   {"model": "qwen3.6", "temperature": 0.3},
    "coder":     {"model": "qwen3-coder", "temperature": 0.2},
    "critic":    {"model": "qwen36-a3b-q4km", "temperature": 0.4},
    "reasoning": {"model": "zaya1-8b", "temperature": 0.3},
    "fast":      {"model": "qwen3.5:4b", "max_tokens": 512},
    "security":  {"model": "qwen3.6", "temperature": 0.3},
})

# Which model handles which role
print(router.model_summary())
# {'planner': 'ollama/qwen3.6', 'coder': 'ollama/qwen3-coder', ...}

# Auto-route based on reasoning mode
result = router.auto_route(decision, mode)
print(result.role)          # "planner"
print(result.latency_ms)    # 1234.5
```

### Mode Overrides

The router adjusts based on system mode:
- **INCIDENT mode** → always uses `fast` model (speed priority)
- **AUDIT mode** → always uses `reasoning` model (depth priority)
- Coding intents → automatically routed to `coder` model

---

## Sessions

Sessions maintain state across multiple turns.

```python
from biobrain.runtime import Session

session = Session(brain, wing="wing_adeo", room="auth_audit")

# Multi-turn conversation
session.turn("scan the authentication endpoints")
session.turn("check for session fixation")
session.turn("test password reset flow")

# Session state
print(session.turn_count)                    # 3
print(session.state.cumulative_confidence)   # 0.72
print(session.state.total_actions)           # 3
print(session.state.total_inhibitions)       # 0

# Change mode mid-session
session.set_mode(SystemMode.AUDIT, "generating report")
session.turn("generate findings report with evidence")

# Approval workflow
print(session.pending_approvals)  # [{request_id, description, ...}]
session.approve()                 # approve most recent
session.deny("req_123")          # deny specific

# Session summary
print(session.summary)
# session=abc123 | turns=4 | actions=4 | inhibitions=0 | ...
```

---

## Orchestration

The orchestrator takes a goal, decomposes it into steps, and executes with guards and replanning.

```python
from biobrain.runtime import Orchestrator

orch = Orchestrator(
    brain,
    max_steps=10,           # hard ceiling
    max_tool_calls=20,      # tool budget
    timeout_seconds=300,    # wall-clock limit
    halt_on_escalation=True,
)

# Run a multi-step goal
result = orch.run("scan auth endpoints, check for IDOR, test session management, generate report")

print(result.completed)       # True/False
print(result.total_steps)     # 4
print(result.total_replans)   # 1 (if the planner adjusted mid-run)
print(result.halt_reason)     # "" or "max_steps_exceeded" or "escalation_required"
print(result.summary)

# Step-level detail
for step in result.steps:
    print(f"Step {step.step_number}: {step.observation}")
    if step.replanned:
        print("  [plan was adjusted after this step]")
```

### Custom Planner

Replace the default delimiter-based planner with an LLM-backed one:

```python
def llm_planner(goal: str) -> list[str]:
    """Use the planner agent to decompose goals."""
    agent = PlannerAgent(router)
    result = agent.plan(goal)
    return result.structured_data["steps"]

orch = Orchestrator(brain, planner=llm_planner)
```

---

## Tools

### Dev Tools (sandboxed)

```python
from biobrain.domain import register_dev_tools

register_dev_tools(sandbox_root="/home/user/project")
```

Available tools:

| Tool | Operation | Description |
|------|-----------|-------------|
| `shell_exec` | EXECUTE | Run shell commands (blocked patterns enforced) |
| `git_status` | READ | Branch, status, recent log |
| `git_diff` | READ | Staged + unstaged diff |
| `git_commit` | WRITE | Add + commit (requires approval in RISK mode) |
| `pytest_run` | EXECUTE | Run tests with structured pass/fail parsing |
| `file_read` | READ | Read file with line numbers and range support |
| `file_write` | WRITE | Write file (sandbox-enforced paths) |
| `file_search` | READ | Grep-like regex search across files |
| `code_search` | READ | AST-aware search for Python functions/classes |

### Sandbox Enforcement

- All file paths resolved relative to `sandbox_root`
- Path traversal (`../../etc/passwd`) blocked
- Dangerous commands blocked: `rm -rf /`, `sudo rm`, `curl|sh`, etc.
- Output truncated at 10K chars
- `shell_exec` not allowed in AUTONOMOUS mode
- Write tools require confirmation in RISK mode

### Pentest Tools

```python
from biobrain.domain import register_pentest_tools

register_pentest_tools()
```

| Tool | Description |
|------|-------------|
| `nmap_scan` | Port/service scan |
| `nuclei_scan` | Vulnerability template scanner |
| `http_probe` | HTTP endpoint behavior check |
| `header_check` | Security header analysis (HSTS, CSP, XFO, etc.) |
| `generate_finding` | LUPISE-format finding record with CVSS 3.1 |

---

## Safety & Policy

### Reflex Layer (pre-reasoning)

Deterministic safety gate that runs BEFORE reasoning:

- **BLOCK**: prompt injection, destructive commands, adversarial sources
- **SANITIZE**: empty input, oversized input (truncated + re-perceived)
- **ESCALATE**: production deployments, mass deletion, privilege changes
- **ROUTE**: help/status/version (bypass reasoning entirely)

### Structured Policy

```yaml
# identity config
allowed_domains: [security, operations, engineering]
forbidden_operations: [delete]
require_approval_for: [execute, configure]
require_evidence_for: [audit, incident]
```

Policy is checked by the executive using `OperationClass` (not substring matching):
- `DELETE` operation → denied if in `forbidden_operations`
- `EXECUTE` operation → requires approval if in `require_approval_for`
- Unknown domain → denied if `allowed_domains` is set

### Mode Modulation

```python
from biobrain.core.enums import SystemMode

brain.mode_manager.transition(SystemMode.AUDIT, "compliance review")
```

| Mode | Effect |
|------|--------|
| NORMAL | Default behavior |
| RISK | Higher risk scoring, tool calls require confirmation |
| AUDIT | Evidence required, retrieval-grounded reasoning forced |
| INCIDENT | Speed priority, higher autonomy ceiling |
| LOW_CONFIDENCE | Broader memory search, more escalation |
| AUTONOMOUS | Full autonomy, reduced escalation |

---

## Tracing & Observability

### Structured Tracing

```python
from biobrain.ops.tracing import Tracer

tracer = Tracer(stream=sys.stderr, live=True)
brain.bus.subscribe(tracer.on_event)

brain.process("check the auth endpoint")
# Live output:
#    0.0ms ingest.input | src=user | trust=trusted | length=24
#    0.3ms perception.classified | intent=security_assessment | op=execute
#    0.5ms attention.scored | prio=NORMAL | risk=0.4 | conf=0.7
#    0.6ms memory.recalled | working=0 | episodic=0
#    0.8ms executive.decided | reasoning=checklist | actions=[tool_call]
#    1.0ms cognition.reasoned | mode=checklist | conf=0.8
#    1.2ms action.executed | type=tool_call | ok=True | tool=nmap
#    1.5ms pipeline.finalized | 1.5ms

# Timeline
tracer.print_timeline()

# Export for analysis
spans = tracer.export_spans()
jsonl = tracer.export_jsonl()

# REX data extraction
model_calls = tracer.model_calls()    # which model, latency, confidence
tool_calls = tracer.tool_calls()      # which tool, success, timing
inhibitions = tracer.inhibitions()    # what was blocked, why
```

### Health Monitor

```python
from biobrain.ops import HealthMonitor

monitor = HealthMonitor()
brain.bus.subscribe(monitor.on_event)

# After processing...
print(monitor.status())
# {"healthy": True, "uptime_s": 120, "traces": 45, "error_rate": 0.02}

print(monitor.metrics())
# {"total_inputs": 45, "avg_latency_ms": 23.4, "p95_latency_ms": 89.2,
#  "reflex_blocks": 3, "reasoning_modes": {"checklist": 12, "direct": 30}}
```

### Audit Logger

```python
from biobrain.core.audit import AuditLogger

audit = AuditLogger(output="./audit.jsonl")
brain.bus.subscribe(audit.on_event)

# Every event → JSONL line
# Every trace → structured audit record
# Every session → session summary record
```

---

## Benchmarking

Compare models on standardized tasks:

```python
from biobrain.agents.benchmark import BenchmarkHarness
from biobrain.cognition.router import ModelRouter

router = ModelRouter.from_config({
    "fast": {"model": "qwen3-coder"},
})

harness = BenchmarkHarness(router)
result = harness.run_suite(
    "coding",
    models=["qwen3-coder", "qwen3.6", "qwen36-a3b-q4km"]
)

harness.print_results(result)
# Benchmark: coding
#   qwen3-coder       | avg_lat=  1234ms | conf=0.820 | kw=0.800 | err=0
#   qwen3.6           | avg_lat=  2100ms | conf=0.750 | kw=0.600 | err=0
#   qwen36-a3b-q4km   | avg_lat=  3200ms | conf=0.680 | kw=0.533 | err=0

# Model-level summary
for s in result.model_summary():
    print(f"{s['model']}: {s['avg_latency_ms']}ms, conf={s['avg_confidence']}")
```

Built-in suites: `coding` (3 tasks), `reasoning` (2 tasks), `security` (1 task).

---

## Playbooks & CyberRange

### OWASP Playbook Engine

```python
from biobrain.domain.playbooks import PlaybookEngine

engine = PlaybookEngine("./configs/playbooks")
engine.load_all()

# Match by trigger keyword
pb = engine.match("authentication bypass")
# → owasp_auth_testing playbook

# Get as checklist memory items (for the reasoner)
items = engine.to_memory_items("owasp_auth_testing")
# → [MemoryItem("PLAYBOOK: owasp_auth_testing..."),
#    MemoryItem("[WSTG-ATHN-01] Test for Credentials..."), ...]

# Get as orchestrator steps
steps = engine.to_orchestrator_steps("owasp_auth_testing")
# → ["WSTG-ATHN-01: Test for Credentials...", ...]
```

### CyberRange Exercise Runner

```python
from biobrain.domain.cyberrange import ExerciseRunner

runner = ExerciseRunner(brain, playbook_dir="./configs/playbooks")

# List available exercises
for ex in runner.list_exercises():
    print(f"{ex['playbook']}: {ex['steps_count']} steps")

# Run an exercise
result = runner.run_exercise(
    exercise_id="EX-AUTH-001",
    playbook="owasp_auth_testing",
    target="https://target.example.com",
    room="room_03",
    difficulty="intermediate",
)

print(result["report_md"])        # Markdown report
print(result["aisec_exercise"])   # AISEC training room format
print(result["total_steps"])      # Steps completed
print(result["completed"])        # All steps ran?

# Auto-match by keyword
result = runner.run_by_trigger("authentication bypass", target="https://...")
```

---

## Configuration

### YAML Config

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

max_steps: 10
timeout_seconds: 300
halt_on_escalation: true
initial_mode: "normal"
audit_log: "./audit.jsonl"
wing: "wing_adeo"
```

### Environment Overrides

All settings can be overridden with `BIOBRAIN_` prefix:

```bash
export BIOBRAIN_PALACE_PATH=/data/palace
export BIOBRAIN_LLM_MODEL=qwen3.6
export BIOBRAIN_INITIAL_MODE=audit
export BIOBRAIN_MAX_STEPS=5
```

Priority: environment > YAML > defaults.

### Loading Config

```python
from biobrain.config import load_config

cfg = load_config("biobrain.yaml")

brain = BioBrain(**cfg.brain_kwargs)
router = ModelRouter.from_config(cfg.router_config)
orch = Orchestrator(brain, **cfg.orchestrator_kwargs)
```

---

## REPL (Interactive Shell)

The REPL provides a rich interactive environment for working with BioBrain.

```bash
biobrain session --wing wing_adeo
```

```
╔══════════════════════════════════════════╗
║  BioBrain REPL — Agentic Runtime v0.7   ║
╠══════════════════════════════════════════╣
║  Session:  abc123def456                  ║
║  Mode:     normal                        ║
║  Wing:     wing_adeo                     ║
╚══════════════════════════════════════════╝
  Type /help for commands, or just type your input.

[normal|t0] >>> scan the auth endpoint
  intent=security_assessment | op=execute | priority=NORMAL | risk=0.4 | ...
  ✓ tool_call [nmap_scan] (1234ms)

[normal|t1] >>> /trace on
  🔍 Live tracing ON

[normal|t1 🔍] >>> check for session fixation
  [   0.0ms] ingest.input | src=user | trust=trusted
  [   0.3ms] perception.classified | intent=security_assessment
  [   0.5ms] attention.scored | prio=NORMAL | risk=0.4
  ...
  intent=security_assessment | op=execute | ...

[normal|t2 🔍] >>> /last
  Perception:
    intent:     security_assessment
    operation:  execute
    risks:      [security_context]
  Salience:
    risk:       0.4
    confidence: 0.7
  Decision:
    reasoning:  checklist
    actions:    [tool_call]

[normal|t2 🔍] >>> /tools
  9 registered tools:
    shell_exec                execute    Run shell command in sandbox
    git_status                read       Git status, branch, recent log
    pytest_run                execute    Run pytest with structured parsing
    file_read                 read       Read file with line numbers
    file_write                write      Write file (sandbox-enforced)
    file_search               read       Grep-like regex search
    code_search               read       AST-aware Python search
    ...

[normal|t2 🔍] >>> /tool file_search pattern=def.*login path=.
  Running file_search [read]...
  ✓ Done (12ms)
    total_matches: 3
    matches: [{"file": "auth.py", "line": 45, "text": "def login(...):"}, ...]

[normal|t2 🔍] >>> /mode audit
  Mode → audit

[audit|t2 🔍] >>> generate findings report
  📋 AUDIT: require evidence and citations
  ...

[audit|t3 🔍] >>> /run scan auth, check IDOR, test sessions, report
  Orchestrating: scan auth, check IDOR, test sessions, report
  goal='scan auth, check IDOR, test sessions, report' | steps=4 | completed=True
    step 1: intent=security_assessment | reasoning=checklist | actions=1/1
    step 2: intent=security_assessment | reasoning=checklist | actions=1/1
    step 3: intent=security_assessment | reasoning=checklist | actions=1/1
    step 4: intent=reporting | reasoning=retrieval | actions=1/1

[audit|t3 🔍] >>> /health
  Healthy:     True
  Traces:      7
  Error rate:  0.00%
  Avg latency: 23.4ms

[audit|t3 🔍] >>> /summary
  session=abc123 | turns=3 | actions=7 | inhibitions=0 | confidence=0.72

[audit|t3 🔍] >>> /quit
```

### REPL Commands

| Category | Command | Description |
|----------|---------|-------------|
| **Session** | `/summary` | Session state summary |
| | `/mode [name]` | Show or change mode |
| | `/approve` | Approve pending action |
| | `/deny` | Deny pending action |
| | `/history [n]` | Show command history |
| **Tools** | `/tools` | List all registered tools |
| | `/tool name k=v` | Run a tool directly |
| **Tracing** | `/trace on\|off` | Toggle live tracing to stderr |
| | `/timeline` | Show full span timeline |
| | `/last` | Inspect last pipeline trace |
| | `/decision` | Show last executive decision |
| | `/reflex` | Show last reflex verdict |
| | `/json` | Last trace as JSON |
| **Memory** | `/memory` | Memory status (working + palace) |
| | `/working` | Show working memory contents |
| **Monitoring** | `/health` | Health check |
| | `/metrics` | Full metrics JSON |
| | `/inhibited` | All inhibitions this session |
| **Orchestration** | `/run goal...` | Run multi-step orchestration |
| **System** | `/help` | Show all commands |
| | `/clear` | Clear screen |
| | `/quit` | Exit |

---

## CLI Reference

```bash
# Single processing
biobrain run "scan the auth endpoint" --mode audit --json
biobrain run "check headers" --source web --wing wing_adeo

# Interactive session
biobrain session --wing wing_adeo --room auth_review
# >>> scan the auth endpoint
# >>> mode audit
# >>> summary
# >>> approve
# >>> quit

# Multi-step orchestration
biobrain orchestrate "scan auth, check tokens, report" --max-steps 5 --json

# Status
biobrain status

# With config file
biobrain -c biobrain.yaml run "test input"

# Debug logging
biobrain -v run "debug this"
```
