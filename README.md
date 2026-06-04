# CTF Skill Pack (Phantom-style)

A thin orchestration + verification layer over a vendored CTF technique corpus.
The split is deliberate:

- **Technique content** (decompiler patterns, packer signatures, anti-debug,
  VM recovery, crypto recipes, web CVE chains) → forked from
  `ljagiello/ctf-skills` and vendored as `ctf-reverse/`, `ctf-crypto/`, etc.
  Do not rewrite this; it is broad and good.
- **This layer** → the parts that corpus does not provide: orchestration,
  routing, the hypothesis/evidence ledgers, the verification gate, the sandbox
  gate, and the search-agent contracts.

## MVP scope (what's here)

```
ctf-skill-pack/
  shared/schemas.md          # the contracts everything reads/writes
  ctf-orchestrator/          # routing, board state, dedup, master flag ledger
  researcher/                # fast single-pass lookup (shared service)
  deepsearcher/              # iterative multi-hop investigation (expensive tier)
  reverse/                   # local reverse doctrine: SKILL.md, references, YAML, later scripts
  flag-discipline/           # verification gate — no solve without it
  exploit-sandbox/           # isolation gate — no execution without it
```

## Skill structure

Each skill is a directory anchored by `SKILL.md` and may also contain:

- `scripts/` for executable helpers
- `references/` for detailed operator or domain material
- `assets/` for templates or static resources
- local YAML or similar support files for machine-readable doctrine

Current reverse skill structure is trending toward:

```text
reverse/
├── SKILL.md
├── REFERENCE.md
├── TECHNIQUES.yaml
├── DECISION_TREE.yaml
└── scripts/              # add executable helpers here later
```

Do not forget `scripts/` when a skill grows beyond pure doctrine. Repetitive or
machine-executable workflows belong there, not embedded inline in `SKILL.md`.

## Build order

1. `shared/schemas.md` — done; everything depends on it.
2. `researcher` — done; `reverse` and the orchestrator consume its schema.
3. `deepsearcher` — done; researcher's escalation target.
4. `reverse` — done; local SOP plus reference material and machine-readable doctrine.
5. `flag-discipline` + `exploit-sandbox` — done; the two gates.
6. `ctf-orchestrator` — done; wires it together.

All category specialists are now present: `reverse`, `crypto-attack`,
`web-exploit`, `binary-pwn`, `forensics`, `stego`, `jail-escape`, `osint`,
`misc`. Each is the same SOP shape — thin decision loop + ledger + handoff,
technique depth from the vendored corpus. Category coverage is complete.

## Deliberately deferred (not bloat-now)

- **Model racing** (verialabs-style parallel solvers) — expensive; only under
  live time pressure, not a default.
- **Memory consolidation / skill evolution** (MemSkill, self-improving-agent) —
  v2. Add a post-solve `consolidate` step once the static pack is proven.
- **Benchmark harness** (InterCode-style) — a test rig for the pack later, not a
  solver now.
- `reverse-symbolic-solver`, `reverse-bytecode-vm` — split out of `reverse` only
  when a challenge actually needs them; the handoff points already exist.

## Two enforced invariants

1. No candidate is `solved` without `flag-discipline`.
2. No unknown artifact runs outside `exploit-sandbox`.

## Canonical package layout

The only canonical runtime implementation lives under:

```text
runtime/ctfrt/
```

Do not use or ship stale duplicated files such as top-level `gate.py`, `contracts.py`,
or `orchestrator.py` outside this directory.
