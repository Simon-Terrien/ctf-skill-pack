# Shared Contracts

Every skill in this pack reads from and writes to these schemas. They are the
interface — change them here, not inside individual skills.

---

## `researcher` output schema (LOCKED)

`reverse`, `ctf-orchestrator`, and every future specialist consume this. It is
designed for the **calling agent**, not for a human reader. Keep `short_answer`
and `actionable_extract` machine-actionable; never return a wall of prose.

```yaml
query:
  original_question:        # verbatim question from the caller
  extracted_tokens: []      # the DISTINCTIVE tokens actually searched
  search_scope:             # fast_lookup

answer:
  short_answer:             # one sentence, the fact
  actionable_extract:       # the command / payload shape / parameter / CVE
  confidence:               # low | medium | high

evidence:
  - source:                 # url, repo path, or local note id
    type:                   # local_notes | official_docs | writeup | web | code_reference
    reliability:            # low | medium | high

handoff:
  needed:                   # true | false
  target:                   # deepsearcher | none
  reason:                   # only if needed
```

Hard rule: `researcher` does **1–3 queries**. If not converging, set
`handoff.needed: true, target: deepsearcher` and stop. Do not loop.

---

## Hypothesis ledger (shared by all solving skills)

The anti-tunnel mechanism. Every solving skill maintains this; the orchestrator
reads it to dedup and to kill dead paths.

```yaml
hypotheses:
  - id:                     # H1, H2, ...
    claim:                  # what we think is true
    confidence:             # low | medium | high
    evidence: []            # observations supporting it (with addresses/offsets)
    next_test:              # the cheapest test that would confirm or kill it
    exit_condition:         # what proves it (e.g. "original binary accepts candidate")
    result:                 # open | confirmed | killed
```

Pivot rule: if a hypothesis produces no new evidence after **two** iterations,
mark it `open` → stale and move to the next-ranked one. Do not keep grinding.

---

## Candidate-flag schema (the only thing `flag-discipline` accepts)

```yaml
candidate:                  # the string
source:                     # how it was derived (skill + method)
flag_format:                # regex if known, else null
format_match:               # true | false | unknown
local_validation:           # passed | failed | not_attempted
oracle_validation:          # passed | failed | not_available
status:                     # raw | format_ok | locally_verified | solved
confidence:                 # low | medium | high
```

A candidate is **`solved`** only at `oracle_validation: passed`, or
`local_validation: passed` when no oracle exists. Nothing below that is a flag.

## Runtime hardening notes

### Candidate validation levels

`Candidate.validation_level` is one of:

- `observed`: candidate string was seen but not proved.
- `format_ok`: candidate matches the expected regex only.
- `reproduced`: a deterministic local reproduction path exists.
- `oracle_accepted`: the platform/oracle accepted the value.

The Gate may only promote a candidate to `solved` when the candidate is either
oracle-accepted or reproduced locally for an oracle-less challenge. Format-only
or observed candidates must remain rejected/raw.

### Sandbox byte fields

`SandboxRequest.stdin`, `SandboxResult.stdout`, and `SandboxResult.stderr` are
bytes in Python and base64 strings on the JSON bus. This keeps binary CTF output
safe across Kafka/JSON serialization.

### Task topics

Specialist tasks are routed to category-specific topics using
`Topics.tasks_for(category)`, e.g. `ctf.tasks.reverse` and
`ctf.tasks.crypto-attack`. The shared `ctf.tasks` name is legacy only.

---

## Task schema

```yaml
task:
  id:              # unique hex ID (auto-generated)
  challenge_id:    # parent challenge
  workdir:         # workspace-relative path
  category:        # reverse | crypto-attack | web-exploit | binary-pwn |
                   # forensics | stego | jail-escape | osint | misc
  artifacts: []    # relative paths under workspace
  flag_format:     # regex or null
  triage:          # dict: type, artifact_types, lessons (from ltm), carry (from handoff), handoff_depth
  sandbox_profile: # default
  created_at:      # unix timestamp
```

---

## Handoff schema

When a specialist reclassifies a challenge it publishes a `Handoff`:

```yaml
handoff:
  challenge_id:    # which challenge
  from_category:   # source specialist category
  target:          # destination category
  reason:          # why reclassified (short string)
  handoff_depth:   # re-route counter; orchestrator rejects at _MAX_HANDOFF_DEPTH=3
  carry:           # dict forwarded to receiving specialist:
    evidence: []             # up to 5 evidence strings from prior steps
    techniques: []           # technique tags accumulated so far
    open_hypothesis_ids: []  # IDs of hypotheses not yet killed
```

---

## ToolAction and ToolCallRecord

Each engine step emits a `ToolCallRecord` trace event so tool invocations are
visible in the append-only audit trail.

```yaml
tool_call_record:
  id:              # unique hex ID
  challenge_id:    # which challenge
  tool:            # one of (ToolAction):
                   #   strings, file, xxd, entropy, archive_list
                   #   objdump_disassembly, objdump_rodata
                   #   readelf_header, readelf_sections, readelf_symbols
                   #   checksec, frequency_analysis
                   #   xor_brute, caesar_brute, base64_decode
                   #   pcap_summary, extract_strings
  artifact:        # artifact path
  result_summary:  # short description of what was found or empty
  exit_code:       # tool exit code or null
  duration_ms:     # wall-clock time of the tool call
  error:           # error message or null
  ts:              # unix timestamp
```

---

## TraceEvent kinds

All components publish to `ctf.traces`. The complete set of `kind` values:

**Lifecycle:**
`routed`, `task_started`, `needs_engine`, `engine_dispatch`, `engine_error`,
`engine_no_candidate`, `challenge_rejected`

**Candidates:**
`candidate_emitted`, `candidate_accepted`, `candidate_rejected`, `gate_verdict`

**Resolution:**
`solved`

**Reverse analysis:**
`reverse_preanalysis`, `reverse_next_action`, `reverse_static_detail`,
`reverse_tool_result`, `reverse_decision_refined`, `reverse_check_path`,
`reverse_transform_path`, `reverse_deterministic_candidate`

**Sandbox:**
`sandbox_request`, `sandbox_result`, `sandbox_timeout`, `sandbox_denied`

**Handoff:**
`handoff`, `handoff_depth_exceeded`

**Tool / memory / intelligence:**
`tool_call_record`, `tool_call_started`, `tool_call_finished`, `tool_call_failed`,
`intelligence_advisory`

---

## Intelligence service contracts

Advisory services provide evidence-backed recommendations to specialists. They
have no execution authority and no Gate bypass.

```yaml
intelligence_question:
  mission_id:      # challenge_id
  requester:       # e.g. "reverse-specialist"
  question:        # natural language question
  context_refs: [] # optional related source IDs
  source_scope:    # internal | external | both
  max_results:     # 1–20

intelligence_answer:
  answer:          # natural language answer or "No match"
  confidence:      # [0.0, 1.0]
  evidence:        # list of EvidenceRef
    - source_id:   # file path or URL
      source_type: # internal_corpus | external_source | trace | writeup | notes | docs | other
      title:        # optional label
      summary:      # excerpt ≤200 chars
      confidence:   # [0.0, 1.0]
      url:          # optional
  recommended_next_action: # one-line suggestion or null
  warnings: []     # degradation notices (e.g. "agentic_rag: no corpus match")
```

Environment-controlled adapters:
- `CTF_INTELLIGENCE_INTERNAL=agentic_rag` → `AgenticRagService` (local corpus keyword RAG)
- `CTF_INTELLIGENCE_EXTERNAL=enhanced_deep_search` → `EnhancedDeepSearchService` (DeepSearcher)
- Default → `NullIntelligenceService`

---

## Board state

The Orchestrator tracks per-challenge board state in working memory:

```yaml
board:
  name:            # challenge name
  board_status:    # running | solved | timed_out | failed
  status:          # legacy field: in_progress | solved
  primary:         # routed category value
  workdir:         # workspace-relative path
  artifacts: []    # registered artifact names
  flags: []        # accepted flag values (populated on solve)
  started_at:      # unix timestamp of on_challenge()
```

`board_status` transitions:
- `running` → `solved` (on_flag with status=solved)
- `running` → `timed_out` (on_trace with kind=engine_no_candidate)
- `running` → `failed` (on_trace with kind=engine_error)

