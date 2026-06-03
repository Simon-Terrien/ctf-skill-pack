---
name: reverse
description: Understand binaries, bytecode, scripts, validators, keygens, packed artifacts, and obfuscated programs in CTF challenges, and recover the flag/key/logic by comprehension. Use for ELF/PE/Mach-O, .NET/JVM/Python bytecode, WASM, APK, firmware, shellcode, or any input-validating program. Owns understanding only — hands off to binary-pwn for exploitation, crypto-attack for cryptanalysis, forensics/stego for embedded data. Calls researcher for unknown constants/markers. All execution routes through exploit-sandbox; no flag is final until flag-discipline passes.
allowed-tools: Read, Bash, Grep, Glob, Write
---

# reverse

## Role & boundaries

Recover validation logic, hidden secrets, transforms, or constraints by
**understanding** the artifact. Owns: static analysis, dynamic observation,
decompilation, constraint extraction, solver construction, local validation.

Does **not** own (hand off cleanly):
- memory-corruption exploitation, ROP, remote exploit dev → `binary-pwn`
- RSA/AES/ECC/hash/PRNG cryptanalysis beyond parameter extraction → `crypto-attack`
- pcap / disk / memory carving → `forensics`; hidden media payloads → `stego`
- restricted-interpreter escapes → `jail-escape`

## Technique content lives in the vendored corpus

This SOP is deliberately thin. Decompiler patterns, packer signatures,
anti-debug catalogues, VM-recovery recipes, per-language decompilation steps →
read from the vendored `ctf-reverse/` reference (forked from
ljagiello/ctf-skills, hardened). Do **not** inline that knowledge here; this
file is the *decision loop and the contracts*, not the encyclopedia.

## Inputs

Artifact path + triage output (type, arch, bitness, stripped?, packed?,
protections) + flag format if known + the assigned `sandbox_profile`.

## Procedure

1. **Confirm triage, don't trust it.** `file`, arch/bitness, linking, entropy
   (packed?), stripped? Wrong classification poisons everything downstream.
2. **Cheap wins first.** `strings` / `rabin2 -z`; hunt flag fragments, creds,
   success/fail messages, format strings, suspicious constants, libc version.
   A large fraction of easy RE dies here. *Ghidra before strings is the #1
   mistake.* Unknown constant or packer marker → call `researcher`.
3. **Static structure.** Ghidra-headless / r2 / rizin → function list, locate
   the validation/check function, the input→compare path, XOR loops, tables.
4. **Branch by shape** (recipes in `ctf-reverse/`):
   - **compare** → dynamic trace (`ltrace`/`strace`), read the expected value.
   - **constraints** → extract them, hand to `reverse-symbolic-solver` (z3/angr).
   - **packed / self-modifying** → unpack or dump in `exploit-sandbox`, restart at 3.
   - **custom VM** → hand to `reverse-bytecode-vm`.
   - **crypto-looking** → extract parameters, hand to `crypto-attack`.
   - **memory corruption** → hand to `binary-pwn`.
5. **Verify.** Run the original artifact against the candidate in the sandbox,
   then submit the candidate to `flag-discipline`. Never declare otherwise.

Maintain the hypothesis ledger (`shared/schemas.md`) throughout. Pivot after two
barren iterations on any hypothesis.

## Outputs

```yaml
classification: { reverse_pattern:, confidence: }
findings: [ { evidence:, implication: } ]
recovered_logic: { description:, constants:, pseudo_code: }
solver: { path:, command: }
candidate: # -> flag-discipline (candidate-flag schema)
handoff: { target:, reason: }   # if it became another domain
```

## Stop & escalate

angr state-explosion past budget → fall back to dynamic. VM semantics or
anti-debug beyond compute budget → human, with the ledger. Core path becomes
exploitation/crypto/forensics → hand off, don't muscle through it.

## Anti-patterns

- Ghidra before `strings`.
- Trusting decompiler pseudocode literally — cross-check the disassembly; it
  lies on optimized/obfuscated code.
- Running anything outside `exploit-sandbox`.
- Treating a patched "Correct!" as proof — recover the real logic or verify the
  flag independently.
- Declaring a flag without local/oracle validation.
- Unbounded angr.
