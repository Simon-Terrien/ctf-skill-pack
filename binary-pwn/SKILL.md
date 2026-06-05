---
name: binary-pwn
description: Exploit memory-corruption vulnerabilities in CTF binaries to gain code execution or leak the flag — stack overflows, ROP, format strings, heap (tcache/fastbin/UAF). Use when the path is exploitation, not comprehension; receives handoffs from reverse. Builds the exploit as a pwntools script, tests locally in exploit-sandbox before remote. Candidates pass flag-discipline.
allowed-tools: Read, Bash, Grep, Glob, Write
---

## Reference Corpus
Local offline technique reference: `vendor/techniques/pwn.md` (When / Tools / Caveats for each technique in this category). Consult before escalating to external search.

# binary-pwn

## Role & boundaries

Turn a memory-corruption bug into the flag. Owns: primitive identification,
mitigation bypass, exploit construction, local→remote delivery.

Does **not** own: understanding the binary's logic when there's no corruption
(→ `reverse`), or crypto in the binary (→ `crypto-attack`). `reverse` hands here
when it finds a *vulnerability* rather than a secret.

## Technique content

ROP recipes, heap-grooming patterns, format-string write primitives, and
mitigation-bypass specifics live in vendored `ctf-pwn/` (forked from ljagiello).
This SOP is primitive → mitigation → exploit routing.

## Inputs

Binary + libc (match it!) + protections from `checksec` + remote endpoint if
any + any vuln location `reverse` already identified.

## Procedure

1. **Confirm the protection set** — `checksec`: NX, ASLR/PIE, canary, RELRO.
   The mitigations dictate the whole strategy; don't skip this.
2. **Identify the primitive** (recipe in `ctf-pwn/`):
   - overflow → ret2win / ret2libc / ROP.
   - format string → leak + arbitrary write.
   - heap → tcache/fastbin dup, UAF, overlap (deep heap → split sub-skill later).
3. **Map mitigation → move:** NX → ROP not shellcode; PIE/ASLR → leak a base
   first; canary → leak or bypass it; Full RELRO → no GOT overwrite, pivot to
   `__malloc_hook`/ret-based. Unknown gadget/offset → `researcher`/one_gadget.
4. **Build as a pwntools script**, parameterized `LOCAL`/`REMOTE`. **Test
   locally in `exploit-sandbox` first** with the matching libc; only then point
   at remote.
5. **Leak → compute → exploit → flag.** Candidate → `flag-discipline`.

Maintain the hypothesis ledger; pivot after two barren iterations.

## Outputs

```yaml
protections:          # checksec summary
primitive:            # overflow | fmt | heap | ...
strategy:             # the mitigation-bypass chain
exploit: { path:, local_ok:, remote_ok: }
candidate:            # -> flag-discipline
handoff: { target:, reason: }
```

## Stop & escalate

No corruption — it's a logic/keygen problem → back to `reverse`. Kernel/browser
pwn or exotic heap beyond budget → human, with the ledger.

## Anti-patterns

- Skipping `checksec` and picking a strategy blind.
- Wrong libc — offsets won't match; verify it.
- Hardcoding offsets without confirming against the actual binary.
- Firing at remote before the local exploit works.
- Shellcode on an NX binary.
