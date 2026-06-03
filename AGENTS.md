# Repository Guidelines

## Project Structure & Module Organization
This repository is a CTF skill pack with one canonical runtime implementation under `runtime/ctfrt/`. Shared contracts live in `shared/schemas.md` and should be updated there first. Each skill has its own `SKILL.md` in a top-level directory such as `reverse/`, `web-exploit/`, or `binary-pwn/`. Runtime tests live in `runtime/tests/`. Avoid adding or shipping duplicate top-level runtime files outside `runtime/ctfrt/`.

## Build, Test, and Development Commands
The runtime is Python-based and has no separate build step.

```bash
cd runtime
PYTHONPATH=. python -m compileall -q ctfrt tests   # syntax check
PYTHONPATH=. python tests/smoke_runtime.py         # local smoke test
PYTHONPATH=. python -m ctfrt.cli solve-local ...   # single-process solve flow
CTF_KAFKA=localhost:9092 CTF_REDIS=redis://localhost:6379/0 python -m ctfrt.run  # distributed runtime
```

## Coding Style & Naming Conventions
Use standard Python 3 style: 4-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and short module names. Keep code direct and explicit; this repository favors small orchestration layers over heavy abstraction. When editing shared behavior, prefer changing schemas or canonical runtime code rather than copying logic into skill folders.

## Testing Guidelines
Add or update tests in `runtime/tests/` when changing runtime behavior. Keep test names descriptive, e.g. `test_engine_weld.py` or `test_cms_cag.py`. Run the smoke test and compile check before submitting changes. If a change affects CLI behavior, validate it with `ctfrt.cli solve-local` using a temporary artifact in `/tmp/`.

## Commit & Pull Request Guidelines
The repository history is not available in this workspace, so use short, imperative commit messages such as `fix runtime gate validation` or `add smoke test coverage`. Keep pull requests focused on one logical change. Include a brief summary, the files or modules changed, and the commands you ran. If behavior changed, note the expected output or include a screenshot/log snippet.

## Security & Configuration Tips
Do not run unknown artifacts outside the sandbox layer, and do not mark a candidate solved without passing `flag-discipline`. Treat `CTF_KAFKA`, `CTF_REDIS`, and `CTF_MEMORY` as environment-specific configuration, not hard-coded defaults.
