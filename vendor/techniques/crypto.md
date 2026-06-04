# Cryptography Attack Techniques

## single-byte XOR
**When:** Blob + small key; ciphertext length == plaintext length; key < 256.
**Tools:** Python: `bytes(b^k for b in blob)` for k in range(256); check printable.
**Caveats:** Try flag-format match first (key that produces `CTF{...}` is the answer).

## caesar / ROT-N
**When:** All-uppercase text with no lowercase; shifted alphabet; short message.
**Tools:** `tr`, Python: `chr((ord(c)-ord('A')-N)%26+ord('A'))` for each shift 0–25.
**Caveats:** Punctuation and numbers are not shifted; wrap-around at Z→A.

## base64 / base32 / base85 decode
**When:** Blob is printable; ends with `=` padding; long string of A-Za-z0-9+/=.
**Tools:** `base64 -d`, Python `base64.b64decode()`; try layers (encoded multiple times).
**Caveats:** Padding might be stripped; try adding `==` if decode fails.

## RSA — small e / Wiener / Fermat
**When:** Public key (n, e, c) provided; n or e is unusually small.
**Tools:** Fermat factorisation if p≈q; Wiener if d<n^(1/4); small-e cube root if e=3.
**Caveats:** Always check GCD(c, n) first (trivially broken if factor divides c).

## RSA — common modulus
**When:** Two ciphertexts encrypted with different e but same n and m.
**Tools:** Extended Euclidean: `e1*s1 + e2*s2 = 1`; recover m = `c1^s1 * c2^s2 mod n`.
**Caveats:** Requires `gcd(e1, e2) = 1`.

## padding oracle
**When:** CBC-mode block cipher; server reveals padding validity without plaintext.
**Tools:** `python-paddingoracle`, manual byte-flip loop.
**Caveats:** Requires chosen-ciphertext oracle; ~128 queries per block.

## hash length extension
**When:** MAC = hash(secret || message); appending data extends hash predictably.
**Tools:** `hashpumpy`, `hlextend`
**Caveats:** Only works on MD-pad constructions (MD5, SHA1, SHA256); not HMAC.

## frequency analysis
**When:** Long monoalphabetic substitution ciphertext.
**Tools:** Count letter frequencies; map to English ETAOIN SHRDLU order.
**Caveats:** Needs ≥200 chars for reliable frequency distribution.
