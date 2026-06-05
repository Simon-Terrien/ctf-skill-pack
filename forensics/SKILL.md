---
name: forensics
description: Recover flags from data artifacts in CTF challenges — pcaps, disk images, memory dumps, logs, and carved/embedded files. Use when the signal is in captured data, not executable logic. Hands off extracted media to stego, extracted validating binaries to reverse, and encrypted blobs to crypto-attack. Candidates pass flag-discipline.
allowed-tools: Read, Bash, Grep, Glob, Write
---

## Reference Corpus
Local offline technique reference: `vendor/techniques/forensics.md` (When / Tools / Caveats for each technique in this category). Consult before escalating to external search.

# forensics

## Role & boundaries

Pull the flag out of data. Owns: pcap analysis, memory/disk forensics, log
timelines, file carving, metadata.

Does **not** own: hidden payloads inside recovered media (→ `stego`), recovered
binaries that validate input (→ `reverse`), or encrypted blobs needing
cryptanalysis (→ `crypto-attack`).

## Technique content

Protocol-decode tricks, volatility plugin chains, carving signatures, and
filesystem-recovery steps live in vendored `ctf-forensics/` (forked from
ljagiello). This SOP is artifact-type → tool routing.

## Inputs

The data artifact + its type from triage (pcap / disk image / memory dump / log
bundle / unknown blob) + any hint about what's hidden.

## Procedure

1. **Type the artifact**, then route (recipes in `ctf-forensics/`):
   - **pcap** → `tshark` filters, follow streams, `--export-objects`, decode the
     application protocol; hunt creds/exfil/embedded files. *Use the CLI, not
     GUI scrolling.*
   - **memory dump** → `volatility3`: pslist, cmdline, filescan → dumpfiles,
     network, registry/bash history.
   - **disk image** → sleuthkit (`fls`/`icat`), deleted-file recovery, fs
     timeline; mount read-only in `exploit-sandbox`.
   - **unknown blob / file** → `binwalk`/`foremost` carve, `exiftool` metadata,
     `xxd` header.
2. **Follow the data, not a tool list** — let what you find drive the next step.
   Carved media → `stego`. Carved binary → `reverse`. Encrypted → `crypto-attack`.
3. **Verify** — extracted flag candidate → `flag-discipline`.

Maintain the hypothesis ledger; pivot after two barren iterations.

## Outputs

```yaml
artifact_type:
findings: [ { evidence:, location: } ]
extracted: []         # files/streams pulled out
candidate:            # -> flag-discipline
handoff: { target:, reason: }
```

## Stop & escalate

Hidden-in-media → `stego`. Recovered binary logic → `reverse`. Encryption →
`crypto-attack`. Corrupt/partial image beyond recovery budget → human.

## Anti-patterns

- Scrolling Wireshark instead of `tshark` filters and object export.
- Skipping `--export-objects` / file carving on a pcap.
- Ignoring file metadata (`exiftool`) before deeper analysis.
- Writing to a disk image instead of mounting read-only.
