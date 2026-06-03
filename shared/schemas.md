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

