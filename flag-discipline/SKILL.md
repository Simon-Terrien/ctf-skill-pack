---
name: flag-discipline
description: Mandatory verification gate for every candidate flag in the pack. Use whenever any solving skill believes it has found a flag, key, or password. Enforces flag-format match, reproducibility, local validation, and oracle validation; rejects hallucinated, partial, or patched-success "solves"; maintains the final flag ledger. No specialist skill may declare a challenge solved without passing through here.
allowed-tools: Read, Bash, Write
---

# flag-discipline

## Role

Stop premature and hallucinated solves. A candidate is a *hypothesis about a
string* until it earns the `solved` status. This gate is the difference between
an agent that solves CTFs and one that confidently reports garbage.

## Inputs

The candidate-flag schema from `shared/schemas.md`.

## Validation levels

```yaml
level_0 raw:               # interesting string, nothing proven
level_1 format_ok:         # matches the expected flag format
level_2 locally_verified:  # accepted by the original artifact/script/service locally
level_3 solved:            # accepted by the CTF oracle / official checker
```

Promotion is monotonic and earned. `solved` requires level_3, or level_2 when
no oracle exists. Anything below `locally_verified` is **not** a flag.

## Final-answer checks (all must pass)

```python
checks = [
    reject_no_flag_format,          # unknown format AND no oracle -> cannot confirm
    reject_unverified_candidate,    # never local- nor oracle-validated
    reject_no_reproduction_path,    # can't be re-derived from the evidence
    reject_patched_binary_success,  # "Correct!" from a patched jump is not a flag
    reject_no_evidence_ledger,      # no hypothesis trail backing it
]
```

## Output

```yaml
candidate:
format_match:
local_validation:
oracle_validation:
status:        # raw | format_ok | locally_verified | solved
confidence:
evidence:      # what backs this — addresses, script, oracle response
```

## Never emit

"The flag is probably…", "this looks like the flag", "solved, maybe". A
candidate is reported at its true level or rejected. No hedged victories.
