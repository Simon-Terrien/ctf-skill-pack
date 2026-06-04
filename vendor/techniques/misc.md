# Miscellaneous CTF Techniques

## barcode / QR code
**When:** Image contains barcode, QR, DataMatrix, or Aztec code.
**Tools:** `zbarimg`, `qrdecode`, `pyzbar`; online: zxing.appspot.com
**Caveats:** Try perspective correction and binarisation if scan fails.

## encoding/decoding chain
**When:** Text looks like multi-layer encoding (base64 → hex → binary → ASCII).
**Tools:** CyberChef (Magic operation), `python base64/codecs`, `xxd -r -p`
**Caveats:** Try ROT13, Atbash, Morse, binary, octal, and brainfuck in sequence.

## polyglot files
**When:** File appears to be two formats simultaneously (JPEG+ZIP, PDF+ELF).
**Tools:** `file`, `binwalk`, `xxd | head`; open with both interpreters.
**Caveats:** ZIP appended to JPEG is valid ZIP (reads from end); use `unzip file.jpg`.

## brainfuck / esoteric languages
**When:** Source code in an esoteric language (BF, Whitespace, Malbolge, etc.).
**Tools:** Online interpreters; `bf.c` or `python bfi`
**Caveats:** Whitespace uses only spaces/tabs/newlines — invisible in editors without show-whitespace.

## prime/modular arithmetic puzzles
**When:** Number theory challenge with large integers; modular inverse, CRT, DLP.
**Tools:** Python `sympy`, `pow(base, exp, mod)`, `gmpy2`
**Caveats:** Small discrete log: baby-step giant-step; large: use index-calculus or sage.

## Git / VCS challenges
**When:** Repository given; flag in commit history, stash, or dangling objects.
**Tools:** `git log --all --oneline`, `git stash list`, `git fsck --unreachable`, `git show`
**Caveats:** `.git/COMMIT_EDITMSG` and reflog often hold last-committed state.
