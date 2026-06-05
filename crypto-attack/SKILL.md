---
name: crypto-attack
description: Recover plaintext, keys, or secrets from cryptographic challenges by cryptanalysis — RSA family, AES modes, classical ciphers, ECC/ECDSA, hashing, and weak PRNGs. Use when the core problem is mathematical, given ciphertext and parameters. Owns the math; receives parameters extracted by reverse, and the token/transport layer from web-exploit. Hands back when the weakness is actually a code bug. All candidates pass flag-discipline.
allowed-tools: Read, Bash, Grep, Glob, Write
---

## Reference Corpus
Local offline technique reference: `vendor/techniques/crypto.md` (When / Tools / Caveats for each technique in this category). Consult before escalating to external search.

# crypto-attack

## Role & boundaries

Break the crypto, given the primitive and its parameters. Owns: RSA/AES/ECC/hash/
PRNG/classical analysis and the solver math.

Does **not** own: extracting parameters out of a binary (→ `reverse` feeds them
here), the HTTP/token transport around a JWT (→ `web-exploit` owns delivery, this
skill owns the JWT's *crypto* weakness), or running an interactive oracle service
(→ `exploit-sandbox`).

## Technique content

Attack recipes, sage snippets, and parameter tells live in vendored
`ctf-crypto/` (forked from ljagiello). This SOP is the *primitive-identification
and routing* loop, not the cookbook.

## Inputs

Ciphertext + everything known: modulus/exponent, mode, IV/nonce, key size,
oracle endpoint, source if available, suspected primitive from `reverse`.

## Procedure

1. **Identify the primitive before touching sage.** Structure usually gives it
   away: modulus + exponent → RSA; 16-byte blocks → AES; repeating-key XOR
   pattern; curve params → ECC. Unknown constant → call `researcher`.
2. **Take the cheap wins first** — they kill most easy crypto:
   - RSA: tiny `e` (cube root), `gcd` across multiple moduli (shared prime),
     Fermat (close primes), Wiener (large `d`), Håstad (broadcast, same `e`).
   - AES: ECB (identical blocks → cut-and-paste), CBC bit-flip, padding oracle,
     CTR/GCM nonce reuse (keystream/auth-key reuse).
   - ECDSA: nonce reuse or biased nonce → lattice/key recovery.
   - Hash: length-extension on `H(secret‖msg)` MACs.
   - PRNG: LCG recovery from outputs; Mersenne Twister state from 624 outputs.
3. **Only then go heavy** — Pohlig-Hellman on smooth order, lattice (LLL),
   index calculus. If it needs a tool, `RsaCtfTool`/`sage`; don't hand-roll what
   they do.
4. **Verify** — recovered plaintext/key reproduces the target; candidate →
   `flag-discipline`.

Maintain the hypothesis ledger; pivot after two barren iterations.

## Outputs

```yaml
primitive:            # rsa | aes | ecc | hash | prng | classical
weakness:             # the specific attack applied
recovered: { plaintext_or_key:, method: }
solver: { path:, command: }
candidate:            # -> flag-discipline
handoff: { target:, reason: }
```

## Stop & escalate

Lattice/DLP beyond compute budget → human. Weakness turns out to be an
implementation bug, not a math one → back to `reverse`/`web-exploit`. Needs a
live oracle → `exploit-sandbox`.

## Anti-patterns

- Reaching for sage before identifying the primitive.
- Skipping the cheap RSA checks (gcd across moduli, small e, Fermat).
- Brute-forcing when the structure hands you a closed-form attack.
- Hand-rolling LLL/factoring instead of using vetted tooling.
