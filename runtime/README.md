# CTF Runtime

Runtime spine for the CTF skill pack.

## Local smoke solve

No Kafka or Redis required:

```bash
cd runtime
printf 'noise CTF{static_win} end\n' > /tmp/note.txt
PYTHONPATH=. python -m ctfrt.cli solve-local \
  --name embedded-flag \
  --category misc \
  --artifact /tmp/note.txt \
  --flag-format 'CTF\{[^}]+\}'
```

Expected output:

```text
CTF{static_win}
```

## Distributed submit

Use Kafka for multi-process/runtime submission:

```bash
CTF_KAFKA=localhost:9092 CTF_REDIS=redis://localhost:6379/0 python -m ctfrt.run
```

Submit from another terminal:

```bash
CTF_KAFKA=localhost:9092 python -m ctfrt.cli submit \
  --name embedded-flag \
  --category misc \
  --artifact ./note.txt \
  --flag-format 'CTF\{[^}]+\}'
```

`submit` refuses the default in-memory bus from a separate process, because that bus is process-local. Use `solve-local` for single-process dev tests.

## Memory backend

```text
No CTF_REDIS set      -> in-memory working memory
CTF_REDIS set         -> Redis working memory
CTF_MEMORY=memory     -> force in-memory
CTF_MEMORY=redis      -> force Redis
```

## Task routing

Tasks use category-specific topics:

```text
ctf.tasks.reverse
ctf.tasks.crypto-attack
ctf.tasks.web-exploit
ctf.tasks.binary-pwn
ctf.tasks.forensics
ctf.tasks.stego
ctf.tasks.jail-escape
ctf.tasks.osint
ctf.tasks.misc
```

This avoids every specialist receiving every challenge. `ctf.tasks` remains only as a legacy/shared topic name constant.

## Validation levels

Candidates carry an explicit proof tier:

```text
observed          string seen, no proof
format_ok         regex matches only
reproduced        deterministic local reproduction exists
oracle_accepted   platform/oracle accepted it
```

Only `reproduced` with local validation, or `oracle_accepted`, can become solved. The Gate rejects observed/format-only candidates, oracle failures, missing evidence, and patched-binary success without oracle acceptance.

## Tests

```bash
cd runtime
PYTHONPATH=. python -m compileall -q ctfrt tests
PYTHONPATH=. python tests/smoke_runtime.py
```
