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

## Local distributed mode

Bring up Kafka and Redis locally:

```bash
cd runtime
docker compose up -d
```

Start the runtime:

```bash
cd runtime
CTF_KAFKA=localhost:9092 CTF_REDIS=redis://localhost:6379/0 PYTHONPATH=. python -m ctfrt.run
```

Submit a challenge from another terminal:

```bash
cd runtime
printf 'noise CTF{distributed_win} end\n' > /tmp/ctf-distributed.txt
CTF_KAFKA=localhost:9092 PYTHONPATH=. python -m ctfrt.cli submit \
  --name distributed-smoke \
  --category misc \
  --artifact /tmp/ctf-distributed.txt \
  --flag-format 'CTF\{[^}]+\}'
```

Troubleshooting Kafka advertised listeners:

- If producers or consumers hang while connecting to `localhost:9092`, check that Docker is exposing port `9092` and that `KAFKA_CFG_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092` matches the host you are using.
- If you are running Docker remotely or through a VM, change the advertised listener from `localhost` to the host-reachable IP or DNS name before starting `docker compose`.
- If the broker starts but `submit` cannot connect, restart the stack with `docker compose down && docker compose up -d` after changing listener settings.

## CLI utilities

Prepare a challenge workspace without solving:

```bash
cd runtime
PYTHONPATH=. python -m ctfrt.cli init-workdir --name seed --artifact /tmp/note.txt --json
```

Inspect triage and routing:

```bash
cd runtime
PYTHONPATH=. python -m ctfrt.cli inspect --name seed --artifact /tmp/note.txt --json
```

Run Gate-only candidate validation:

```bash
cd runtime
PYTHONPATH=. python -m ctfrt.cli validate-candidate \
  --challenge-id demo \
  --candidate 'CTF{ok}' \
  --flag-format 'CTF\{[^}]+\}' \
  --validation-level reproduced \
  --local-validation passed \
  --oracle-validation not_available \
  --evidence 'unit reproduction' \
  --json
```

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

## Logging and redaction

Runtime logs are controlled with:

```text
CTF_LOG_LEVEL=INFO|DEBUG|...
CTF_LOG_JSON=1          # JSON log lines
```

Flags are redacted in logs and persisted traces by default. To disable redaction
for local debugging only:

```bash
CTF_DEBUG_FLAGS=1 PYTHONPATH=. python -m ctfrt.cli solve-local ...
```

## Sandbox policy

Sandbox execution is Docker-based and supports these knobs:

```text
CTF_SANDBOX_IMAGE
CTF_SANDBOX_READ_ONLY_ROOT
CTF_SANDBOX_CPUS
CTF_SANDBOX_MEMORY
CTF_SANDBOX_PIDS_LIMIT
CTF_SANDBOX_FILE_SIZE
CTF_SANDBOX_SECCOMP
CTF_SANDBOX_APPARMOR
```
