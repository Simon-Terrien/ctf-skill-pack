# Binary Exploitation Techniques

## ret2libc
**When:** Stack overflow; NX enabled; no PIE; libc present.
**Tools:** `pwntools` ROP chains; `ROPgadget --binary elf`; `puts@plt` to leak libc base.
**Caveats:** Align stack to 16 bytes before `system()`; ASLR needs a leak first.

## ROP chain
**When:** NX + ASLR; need to chain gadgets to execute shellcode-equivalent primitives.
**Tools:** `ROPgadget`, `ropper`, `pwntools.rop`
**Caveats:** Gadgets ending in `ret` only; avoid gadgets that corrupt needed registers.

## format string exploitation
**When:** User input passed directly to `printf()` without format arg.
**Tools:** `%p.%p.%p.%p` to leak stack; `%N$n` for arbitrary write.
**Caveats:** Count offset to target with `%1$p ... %100$p`; write 4 bytes at a time.

## heap exploitation
**When:** UAF, double-free, or overflow on heap allocations.
**Tools:** `pwndbg`, `heapinfo`, `tcache poison` (glibc ≥2.26).
**Caveats:** Check glibc version; tcache differs from fastbin; safe-linking from glibc 2.32.

## stack canary bypass
**When:** Stack overflow but canary check present.
**Tools:** Leak canary via format string or info leak; overwrite with same value.
**Caveats:** Canary always ends in `\x00` on Linux; brute-force viable on 32-bit fork servers.

## shellcode injection
**When:** Stack/heap executable; no NX; write shellcode to controlled buffer.
**Tools:** `pwntools.shellcraft`, `asm()`, `msfvenom`
**Caveats:** Avoid null bytes if strcpy/gets used; align to 16 bytes for SSE.
