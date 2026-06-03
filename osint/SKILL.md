---
name: osint
description: Solve open-source-intelligence CTF challenges — locating information from public sources given a challenge-provided target (a handle, image, document, coordinates). Use for geolocation, metadata, username/account pivoting, and public-record lookups. Largely CTF tradecraft layered over researcher/deepsearcher. Strictly scoped to the challenge target. Candidates pass flag-discipline.
allowed-tools: Read, Bash, Grep, Glob, WebSearch, WebFetch, Write
---

# osint

## Role & boundaries

Find the public information the challenge points to. Owns: the OSINT tradecraft
(EXIF/geolocation, reverse image search, handle pivoting, public records) and
the CTF-specific framing.

Delegates the actual searching to `researcher` (fast lookups) and `deepsearcher`
(multi-hop). This skill adds the tradecraft and the guardrail; it is not a second
search engine.

## Scope discipline (hard)

Only the **challenge-provided target**. CTF OSINT targets are fictional or
organizer-planted. Do not pursue, profile, or aggregate data on real private
individuals; do not touch real accounts or infrastructure. If a challenge seems
to point at a real person's private data, stop and flag it — that's outside CTF
scope.

## Inputs

The seed artifact: a username, image, document, partial name, coordinates, or
profile, plus the question the flag answers.

## Procedure

1. **Extract everything from the artifact itself first.** `exiftool` on images
   (GPS, device, timestamps); document metadata/author; visible landmarks/text.
2. **Pivot from the seed:** reverse image search, handle reuse across platforms,
   timestamps → events. Hand each concrete lookup to `researcher`; escalate
   multi-hop trails to `deepsearcher`.
3. **Triangulate** — corroborate across ≥2 independent sources before treating a
   lead as fact (the `deepsearcher` evidence ledger applies).
4. Candidate → `flag-discipline`.

## Outputs

```yaml
seed:
leads: [ { claim:, sources: [], confidence: } ]
candidate:            # -> flag-discipline
```

## Stop & escalate

Trail runs cold within budget → `deepsearcher` for a deeper pass, else human.
Target appears to be a real private individual → stop, flag for review.

## Anti-patterns

- Re-implementing search instead of delegating to `researcher`/`deepsearcher`.
- Single-source conclusions on a geolocation or identity.
- Drifting off the challenge target onto real people or live infrastructure.
- Ignoring the artifact's own metadata before searching outward.
