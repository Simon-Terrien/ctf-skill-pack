# Reverse Engineering Techniques

## strings
**When:** First pass on any binary; look for plaintext flags, error messages, passwords.
**Tools:** `strings`, `grep`, `binwalk --extract`
**Caveats:** Flags encoded/obfuscated won't appear; follow with dynamic analysis.

## static disassembly
**When:** Binary has no source; need to understand control flow around compare.
**Tools:** `objdump -d -M intel`, `ghidra`, `radare2 -A`
**Caveats:** Stripped binaries have no symbol names; use PLT/GOT stubs to identify libc calls.

## strcmp / memcmp patch
**When:** Binary compares input to a fixed string with strcmp/memcmp.
**Tools:** `ltrace`, `gdb` (breakpoint on strcmp), `objdump` + python re-encode
**Caveats:** Stripped binaries label the call as `<sym@plt+offset>`; filter `@plt>` not `@plt`.

## xor crackme
**When:** Input is XOR-transformed before compare; rodata holds ciphertext.
**Tools:** Static: reconstruct from objdump_rodata + SIMD movdqa/movups pattern.
Dynamic: ltrace, strace.
**Caveats:** Self-referential XOR (buf XOR xor_sum_of_input): check if XOR(buf)=0 for odd-length keys.

## angr / symbolic execution
**When:** Complex constraint (loop with many branches); manual analysis too slow.
**Tools:** `angr`, `z3`
**Caveats:** Path explosion on long loops; set `max_steps` and avoid forking on irrelevant branches.

## stripped binary analysis
**When:** No symbol table (`stripped` flag set, no `.symtab`).
**Tools:** `file`, `readelf -h`, `objdump -d` (functions labeled by PLT offset)
**Caveats:** Helper calls appear as `<libc_sym@plt+0xNNN>` — include these in helper analysis.

## ROP / ret2libc
**When:** Stack overflow found; NX enabled; want to call system("/bin/sh").
**Tools:** `ROPgadget`, `pwntools`, `ropper`
**Caveats:** ASLR; need a libc leak first. Check for ret2plt → puts → libc base.

## dynamic tracing
**When:** Need runtime values (keys, buffers) without reversing.
**Tools:** `ltrace -e strcmp+memcmp`, `strace -e read+write`, `frida`
**Caveats:** May not work on statically linked or heavily obfuscated binaries.
