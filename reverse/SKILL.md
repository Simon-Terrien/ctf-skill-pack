---
name: reverse
description: Understand binaries, bytecode, scripts, validators, keygens, packed artifacts, and obfuscated programs in CTF challenges, and recover the flag/key/logic by comprehension. Use for ELF/PE/Mach-O, .NET/JVM/Python bytecode, WASM, APK, firmware, shellcode, or any input-validating program. Owns understanding only — hands off to binary-pwn for exploitation, crypto-attack for cryptanalysis, forensics/stego for embedded data. Calls researcher for unknown constants/markers. All execution routes through exploit-sandbox; no flag is final until flag-discipline passes.
allowed-tools: Read, Bash, Grep, Glob, Write
---

## Reference Corpus
Local offline technique reference: `vendor/techniques/reverse.md` (When / Tools / Caveats for each technique in this category). Consult before escalating to external search.

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

## Inputs

Artifact path + triage output + reverse pre-analysis summary + flag format if
known + the assigned `sandbox_profile`.

The current runtime can already provide, for local ELF/PE/Mach-O artifacts:
- kind / magic
- size
- sha256
- capped printable strings
- imported symbols when `readelf` or `objdump` is available
- section list when available
- ELF hints such as `pie`, `stripped`, and `dynamically_linked`

Treat that summary as the starting evidence, not as ground truth. Confirm and
extend it before escalating.

## Skill structure

This skill is not just `SKILL.md`.

Use:
- `SKILL.md` for activation instructions and bounded decision flow
- `REFERENCE.md` for operator-facing command/reference material
- `TECHNIQUES.yaml` for machine-readable technique vocabulary
- `DECISION_TREE.yaml` for machine-readable next-action rules
- `scripts/` later for executable reverse helpers once a workflow becomes repetitive or runtime-adjacent

Do not bury executable procedures in prose once they are stable enough to live
as helpers.

## Reverse Step Loop v1

This is the current doctrine for a bounded reverse analysis loop. The loop is
ordered, evidence-driven, and intentionally conservative.

1. Artifact triage
2. Cheap wins
3. Static detail pass
4. String-reference analysis
5. Imports/symbols analysis
6. Disassembly summary
7. Decide whether sandboxed dynamic tracing is justified
8. Validate or hand off

The loop must stop after a bounded number of barren steps. Do not thrash across
tools without a new hypothesis.

## Procedure

### 1. Artifact triage

Confirm basic shape before deeper analysis:
- binary kind: ELF / PE / Mach-O / script / bytecode / text
- architecture and bitness
- static vs dynamic linking
- stripped vs symbol-rich
- PIE / non-PIE where available
- likely input mode: argv, stdin, file, network stub, environment

Questions to answer immediately:
- Is this actually a reverse problem, or is it crypto / pwn / stego in disguise?
- Is the artifact executable code, a wrapper, a packer, a loader, or data?
- Does the binary expose obvious success/failure strings or usage text?

If triage changes the challenge class, hand off early.

### 2. Cheap wins

Do the lowest-cost, highest-signal checks first:
- inspect strings
- inspect imported symbols
- inspect section names
- inspect usage/help text
- inspect obvious constants, keys, prompts, markers, and file paths

Cheap wins to look for:
- plaintext flag fragments
- success / failure messages
- format strings
- `strcmp`, `memcmp`, `puts`, `printf`, `strlen`, `fgets`, `scanf`, `read`
- suspicious XOR / table / alphabet constants
- embedded filenames, URLs, or debug remnants

If a cheap win gives a complete, defensible recovery path, stop and validate.

### 3. Static detail pass

If cheap wins are not enough, summarize the artifact structurally:
- likely main path / check path
- input acquisition path
- comparison path
- transformation path
- error / success branch locations
- any loop over input bytes
- any table lookup, XOR, add/subtract, rotate, checksum, or encoding stage

The output of this pass should be a short model of the validation logic, not a
wall of disassembly.

### 4. String-reference analysis

If strings are meaningful, treat them as anchors:
- find success and failure strings first
- find usage / prompt strings second
- find unusual constants / labels third

Then ask:
- Which function references the success/failure strings?
- Is there a nearby compare, branch, or transform loop?
- Does the string reference isolate a single validation function?
- Are there multiple checks or staged validations?

If strings clearly anchor the check function, prefer following those references
before broad disassembly.

### 5. Imports / symbols analysis

Use imports and section data to narrow the likely behavior:
- `strcmp` / `memcmp` suggest direct comparison paths
- `strlen`, `strncpy`, `fgets`, `scanf`, `read` suggest input handling shape
- crypto-looking imports suggest parameter extraction and possible handoff
- dynamic loader / unpacker imports may indicate packing or staged loading
- presence or absence of `.symtab`, `.dynsym`, and debug sections affects how
  aggressive static lifting can be

Questions to answer:
- Is the checker probably direct compare, encoded compare, or derived constraint?
- Is the artifact likely stripped enough that strings/imports are insufficient?
- Do imports imply local-only validation, or a need for later sandboxed tracing?

### 6. Disassembly summary

Only after the earlier passes, produce a concise disassembly summary:
- probable entry / check function
- input source
- comparison primitive
- transform primitive
- constants / tables involved
- exit conditions

For optimized or stripped binaries, trust the disassembly over pseudocode when
they disagree. The goal is a recovery hypothesis the runtime can later test.

### 7. When to escalate to sandboxed dynamic tracing

Escalate only when static work has produced a concrete reason.

Good reasons to escalate:
- imports strongly suggest direct compare, but expected value is only visible at runtime
- strings identify success/failure, but static xrefs do not recover the compared bytes
- argument handling is clear, but transform output is cumbersome to recover statically
- unpacking / staging / loader behavior blocks static understanding
- anti-optimization or stripped control flow obscures a likely simple check

Do **not** escalate yet if:
- you have not finished strings/imports/section review
- you do not know what question dynamic tracing is supposed to answer
- you are hoping execution will “just reveal the flag”

Sandboxed dynamic tracing is later-only and must stay bounded:
- `ltrace` / `strace` later, sandbox-only
- `gdb` later, sandbox-only
- no host execution

### 8. Verify, hand off, or stop

Possible outcomes:
- recover candidate and produce a reproduction hypothesis
- extract transform/constraints for later solver work
- hand off to `binary-pwn`, `crypto-attack`, `forensics`, or `stego`
- stop with a clear blocked reason and evidence ledger

Maintain the hypothesis ledger throughout. Pivot after two barren iterations on
the same hypothesis.

## Outputs

```yaml
classification: { reverse_pattern:, confidence: }
findings: [ { evidence:, implication: } ]
recovered_logic: { description:, constants:, pseudo_code: }
solver: { path:, command: }
candidate: # -> flag-discipline (candidate-flag schema)
handoff: { target:, reason: }   # if it became another domain
```

## Evidence requirements

Every claimed finding should be tied to an observable:
- string
- symbol / import
- section / header fact
- disassembly observation
- sandboxed dynamic trace later, if escalation is justified

Minimum evidence before proposing a candidate:
- where input is read
- where it is checked or transformed
- what constant / expected value / rule is being enforced
- why the proposed candidate matches that rule

Patched success text is not enough. A guess from one string is not enough. A
single decompiler snippet without cross-check is not enough.

## Stop conditions

Stop and reassess when:
- two consecutive steps produce no new evidence
- the current hypothesis cannot explain both success and failure paths
- imports / strings point clearly to another specialty
- unpacking / anti-debug / staged loading exceeds the current budget
- symbolic execution or broad solver work would be premature

Escalate to later sandboxed dynamic tracing only with a concrete question.

## Reverse Step Loop v1 implementation plan

The local runtime already covers:
- artifact registration under a safe workspace
- read-only reverse pre-analysis for local binaries
- BioBrain prompt injection with strings/imports/sections summary

The next bounded runtime loop should add, in order:
1. reverse static detail step
2. string-reference step
3. imports/symbols decision step
4. disassembly summary step
5. sandboxed dynamic tracing step only when justified

Each step should emit trace evidence and stop after bounded barren iterations.

## Anti-patterns

- Ghidra before `strings`.
- Dynamic tracing before a strings/imports pass.
- Using runtime output as a substitute for understanding the check path.
- Trusting decompiler pseudocode literally — cross-check the disassembly; it
  lies on optimized/obfuscated code.
- Running anything outside the sandbox.
- Treating a patched "Correct!" as proof — recover the real logic or verify the
  flag independently.
- Declaring a flag without local/oracle validation.
- Unbounded angr.
- Escalating to dynamic tracing without a concrete question to answer.
