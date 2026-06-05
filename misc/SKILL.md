---
name: misc
description: Solve uncategorized CTF challenges — exotic encodings, esoteric languages, QR/barcodes, audio/DTMF, weird file formats, game theory, commitment schemes. Use when a challenge fits no other category and the core task is recognizing and decoding/transforming something unusual. Leans heavily on researcher to identify the format. Candidates pass flag-discipline.
allowed-tools: Read, Bash, Grep, Glob, WebSearch, Write
---

## Reference Corpus
Local offline technique reference: `vendor/techniques/misc.md` (When / Tools / Caveats for each technique in this category). Consult before escalating to external search.

# misc

## Role & boundaries

The catch-all for the genuinely uncategorizable. Owns: format/encoding
recognition and the decode/transform. The moment it resolves into a real
category (it's actually crypto, a binary, a web app), route there — `misc` is a
holding pattern, not a destination.

## Technique content

Format catalogues — RTF tag extraction, SMS PDU, UTF-9/odd encodings, MaxiCode/
2D barcodes, DTMF + T9, music-note encodings, esolang interpreters — live in
vendored `ctf-misc/` (forked from ljagiello). This SOP is recognize → decode →
route.

## Inputs

The artifact and whatever's odd about it (unprintable bytes, a strange alphabet,
audio tones, a visual code).

## Procedure

1. **Characterize the oddity.** Charset/alphabet, byte distribution, visual or
   audio structure, any header. If you can't name it, hand the distinctive
   tokens to `researcher` — identification is most of the battle here.
2. **Decode/transform** with the recipe from `ctf-misc/`. Many misc challenges
   are a single recognition step away from trivial.
3. **Re-route the instant it's classifiable** — crypto → `crypto-attack`,
   binary → `reverse`, jailed interpreter → `jail-escape`, hidden-in-media →
   `stego`.
4. Candidate → `flag-discipline`.

## Outputs

```yaml
recognized_as:        # encoding/format/puzzle type
decode_method:
candidate:            # -> flag-discipline
handoff: { target:, reason: }   # if it became a real category
```

## Stop & escalate

Resolves into another category → route there immediately. Unidentifiable format
after a `researcher` + `deepsearcher` pass → human.

## Anti-patterns

- Forcing a real-category challenge to stay in `misc`.
- Guessing the encoding instead of identifying it via `researcher`.
- Over-engineering a decoder when a known tool/format exists.
