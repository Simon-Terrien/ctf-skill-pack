---
name: ctf-orchestrator
description: Coordinate CTF solving across the skill pack. Use when working one or more CTF challenges and you need routing, board-level state, hypothesis dedup, and the master flag ledger. Invokes triage first, routes artifacts to the correct specialist, holds the cross-challenge view the specialists cannot see, and enforces that nothing is declared solved without flag-discipline.
allowed-tools: Read, Write, Grep, Glob
---

# ctf-orchestrator

## Role

The only component holding global board state. Specialists are tunnel-visioned by
design; the orchestrator does the cross-challenge reasoning (shared infra, reused
keys, chained challenges) and prevents duplicate work. It also *is* the
validate/dedup layer — that is not a separate skill.

## Responsibilities

1. Receive challenges + artifacts; run `ctf-triage` first unless triage exists.
2. Route by **evidence, not by the challenge's category label**.
3. Maintain the board ledger (below) and the master flag ledger.
4. Dedup and rank hypotheses across specialists; kill confirmed-dead paths.
5. Gate every candidate through `flag-discipline` before marking solved.
6. Escalate to human when automated analysis stalls against budget.

## Routing

```text
binary / bytecode / packed / validator / keygen   -> reverse
memory corruption, exploitation, remote shell      -> binary-pwn
RSA/AES/ECC/hash/PRNG cryptanalysis                -> crypto-attack
pcap / disk image / memory dump / logs             -> forensics
hidden data in image/audio/video/archive           -> stego
HTTP app / API / JWT / SSRF / SSTI / SQLi           -> web-exploit
restricted interpreter / sandbox bypass            -> jail-escape
a scoped factual question (any specialist may ask) -> researcher (-> deepsearcher)
```

Routing is by evidence: a JWT challenge may be web + crypto; a packed binary may
be reverse + forensics; an APK may be mobile-re + crypto. Route to the strongest
evidence, keep the alternates as open hypotheses.

## Board ledger

```yaml
challenge:
  name:
  category_hint:
  artifacts: []
  flag_format:
  status:            # open | in_progress | solved | stuck
hypotheses: []       # shared hypothesis-ledger schema, merged across specialists
rejected_paths: []   # so nothing gets retried
flags: []            # candidate-flag schema entries, master ledger
```

## Do not

- Let any specialist declare a final solve without `flag-discipline`.
- Run untrusted artifacts outside `exploit-sandbox`.
- Retry a `rejected_path` without genuinely new evidence.
- Spawn parallel model races by default — only under live time pressure on a
  high-priority challenge where cheap triage has already failed.
