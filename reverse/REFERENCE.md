# Reverse Reference

This file is an operator reference for the local reverse workflow. It is not a
runtime contract and it does not authorize host execution.

When this skill needs repeatable executable helpers, add them under
`reverse/scripts/` and reference them from `SKILL.md`. Keep `REFERENCE.md` for
human-readable guidance and command patterns, not as a substitute for scripts.

## Current read-only workflow

Start with:
- file kind / magic
- size
- sha256
- printable strings
- imports
- sections
- ELF hints such as `pie`, `stripped`, and `dynamically_linked`

Current runtime support for that pre-analysis lives in `runtime/ctfrt/reverse_tools.py`.

## strings workflow

Use strings first when the artifact is a local binary:

```bash
strings -n 4 ./artifact
strings -n 4 ./artifact | rg 'flag|pass|wrong|correct|usage|key|secret'
```

Look for:
- usage text
- success / failure messages
- format strings
- filenames and paths
- URLs
- suspicious constants
- obvious compare targets

Questions:
- Which strings define the success and failure branches?
- Which strings indicate the expected input shape?
- Which strings are likely referenced near the validation logic?

## readelf commands

For ELF artifacts:

```bash
readelf -h -l ./artifact
readelf -S ./artifact
readelf -Ws ./artifact
readelf -d ./artifact
```

Use them to answer:
- is it ELF64 or ELF32?
- is it PIE (`Type: DYN`) or non-PIE (`Type: EXEC`)?
- is it dynamically linked?
- is `.symtab` present, or is the binary stripped?
- which imported symbols narrow the likely validation path?

## objdump commands

When `readelf` is absent or when you want a second view:

```bash
objdump -x ./artifact
objdump -d ./artifact
objdump -Mintel -d ./artifact
objdump -s -j .rodata ./artifact
```

Use them to answer:
- what sections exist?
- what imported symbols are unresolved?
- where do success/failure strings sit in `.rodata`?
- what comparison or transform instructions occur near the likely check path?

## Static detail workflow

After strings/imports:
1. locate likely success/failure anchors
2. identify the function that references them
3. identify input acquisition
4. identify compare or transform logic
5. summarize the validation rule in plain language

The output should be short:
- input source
- check function
- transform/compare primitive
- constants/tables
- success condition

## Dynamic tracing later, sandbox-only

These are later escalation tools only:

```bash
ltrace ./artifact ...
strace ./artifact ...
```

Use later, sandbox-only, when:
- direct compare is strongly suspected
- runtime-resolved values matter
- staged unpacking or loader behavior blocks static understanding

Do not use them as the first move.

## gdb later, sandbox-only

Later-only, sandbox-only:

```bash
gdb ./artifact
```

Use only when a bounded debugging question exists:
- where does the compared buffer come from?
- what value is produced just before the check?
- which branch distinguishes success from failure?

Do not use `gdb` just to wander.

## Anti-patterns

- Skipping strings and imports and jumping straight to disassembly.
- Treating one success string as proof of a solution.
- Running the binary on the host.
- Using dynamic tools without a specific question.
- Letting the tool sequence become unbounded.
