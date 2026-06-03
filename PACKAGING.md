# Packaging layout

This repository has one canonical source of truth:

```text
ctf-skill-pack/
├── <skill-name>/SKILL.md
├── shared/schemas.md
└── runtime/ctfrt/*.py
```

Do not ship duplicated top-level runtime files such as `gate.py`, `contracts.py`,
or `orchestrator.py` outside `runtime/ctfrt/`. Those were early convenience
copies and can become stale.

Recommended final ZIP command from the parent directory:

```bash
cd /path/to/parent
zip -r ctf-skill-pack-final.zip ctf-skill-pack \
  -x '*/__pycache__/*' '*.pyc' '.pytest_cache/*'
```
