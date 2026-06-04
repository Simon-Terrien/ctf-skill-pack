# Packaging layout

This repository has one canonical source of truth:

```text
ctf-skill-pack/
├── <skill-name>/
│   ├── SKILL.md
│   ├── scripts/        # optional executable helpers
│   ├── references/     # optional operator/docs material
│   ├── assets/         # optional static resources/templates
│   └── *.yaml          # optional machine-readable doctrine/config
├── shared/schemas.md
└── runtime/ctfrt/*.py
```

Do not ship duplicated top-level runtime files such as `gate.py`, `contracts.py`,
or `orchestrator.py` outside `runtime/ctfrt/`. Those were early convenience
copies and can become stale.

When adding or updating a skill:
- keep `SKILL.md` as the activation entrypoint
- put executable helpers in `scripts/`
- put detailed references in `references/`
- keep machine-readable doctrine beside the skill when it is intended to drive later runtime behavior

Recommended final ZIP command from the parent directory:

```bash
cd /path/to/parent
zip -r ctf-skill-pack-final.zip ctf-skill-pack \
  -x '*/__pycache__/*' '*.pyc' '.pytest_cache/*'
```
