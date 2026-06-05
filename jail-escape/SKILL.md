---
name: jail-escape
description: Escape restricted interpreters and sandboxes in CTF challenges — pyjails, bash jails, Lua/Ruby/JS sandboxes, restricted-charset or filtered eval. Use when the challenge is bypassing language or shell constraints to read the flag or get execution. Mostly pure reasoning, minimal tooling; all interaction with the target runs in exploit-sandbox. Candidates pass flag-discipline.
allowed-tools: Read, Bash, Grep, Glob, Write
---

## Reference Corpus
Local offline technique reference: `vendor/techniques/jail.md` (When / Tools / Caveats for each technique in this category). Consult before escalating to external search.

# jail-escape

## Role & boundaries

Defeat the restriction and reach the flag/exec. Owns: filter analysis and
payload construction for restricted interpreters and shells.

Does **not** own: native binary sandboxes via memory corruption (→ `binary-pwn`)
or web-layer sandboxes (→ `web-exploit`). The target itself runs only inside
`exploit-sandbox`.

## Technique content

Concrete escape chains — `func_globals` module walks, `__class__`/`__subclasses__`,
f-string config injection, `TracePoint` (Ruby), Lua table-index bypass, bash
brace/glob tricks — live in vendored `ctf-misc/pyjails` and friends (forked from
ljagiello). This SOP is the analysis loop.

## Inputs

The jail source or the interactive endpoint, and the observed filter behavior.

## Procedure

1. **Enumerate the restriction precisely.** What characters/keywords/builtins
   are blocked? What's the eval surface (eval/exec, f-string, format, template)?
   Understanding the filter beats spraying payloads.
2. **Find the reachable primitive** from `ctf-misc/`: an object graph to walk to
   `os`/`subprocess`, an unfiltered builtin, an encoding that dodges the charset
   filter, a config/import side-channel.
3. **Build the minimal escape**, respecting the exact constraints (no banned
   chars, length caps).
4. **Run it in `exploit-sandbox`**, read the flag, candidate → `flag-discipline`.

Maintain the hypothesis ledger; each blocked attempt narrows the filter model.

## Outputs

```yaml
jail_type:            # pyjail | bash | lua | ruby | js | ...
restriction:          # what's filtered
escape:               # the working payload + why it dodges the filter
candidate:            # -> flag-discipline
handoff: { target:, reason: }
```

## Stop & escalate

It's actually memory corruption → `binary-pwn`. Filter is genuinely complete and
no primitive exists within budget → human, with the filter model.

## Anti-patterns

- Spraying payloads before modeling exactly what's blocked.
- Ignoring length/charset caps and shipping a payload that can't fit.
- Assuming a builtin is reachable without checking it's unfiltered.
