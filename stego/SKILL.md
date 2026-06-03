---
name: stego
description: Extract hidden data from media in CTF challenges — images, audio, video, archives. Use when a payload is concealed in a file rather than encrypted or compiled. Receives carved media from forensics; hands extracted binaries to reverse and encrypted blobs to crypto-attack. A thin tool-runner. Candidates pass flag-discipline.
allowed-tools: Read, Bash, Grep, Glob, Write
---

# stego

## Role & boundaries

Pull concealed payloads out of media. Owns: LSB, embedded-file extraction,
metadata-hidden secrets, spectrogram/audio tricks.

Does **not** own: cryptanalysis of what comes out (→ `crypto-attack`), or a
recovered binary's logic (→ `reverse`). Receives carved media from `forensics`.

## Technique content

Tool flags and channel-specific tricks (F5 DCT, palette tricks, audio T9/DTMF,
jigsaw reassembly) live in vendored `ctf-forensics/` stego references (forked
from ljagiello). This SOP is the sweep order.

## Inputs

The media file + its type + any hint (a passphrase seen elsewhere, a tool name).

## Procedure

1. **Metadata + structure first.** `exiftool`, `xxd` header, `binwalk` — the
   passphrase or appended file is often sitting in plain sight.
2. **Sweep by media type:**
   - **image** → `zsteg` (PNG/BMP), `steghide extract` (JPEG/BMP/WAV/AU, try
     known passphrase), LSB extraction, `stegsolve` planes.
   - **audio** → spectrogram (look for text/QR), LSB, DTMF/T9 decode.
   - **archive/polyglot** → `binwalk`/`foremost` carve, check for appended data.
3. **Route what falls out** — binary → `reverse`, encrypted → `crypto-attack`,
   another media layer → recurse.
4. Candidate → `flag-discipline`.

## Outputs

```yaml
channel:              # image_lsb | steghide | spectrogram | appended | ...
extracted: []
candidate:            # -> flag-discipline
handoff: { target:, reason: }
```

## Stop & escalate

Extracted blob needs crypto → `crypto-attack`. Recovered binary → `reverse`.
Nothing after a full sweep → re-examine the `forensics` carve; the wrong layer
may have been handed over.

## Anti-patterns

- Skipping `exiftool`/header inspection before heavy tools.
- Not trying a passphrase already recovered elsewhere in the challenge.
- Forgetting the audio spectrogram — a classic.
- Treating partial output as the flag without `flag-discipline`.
