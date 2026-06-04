"""Independent candidate verification.

The trust fix: an engine claiming `local_validation="passed"` is not enough. The
Verifier re-derives ground truth from the *artifact* and confirms the candidate
against it, so a lying engine cannot promote a wrong flag. The reproduction
recipe names a METHOD and the ARTIFACT; verification reads truth from the file or
the binary's behavior — never from engine-supplied expected values.

Methods:
  reencode_xor   — read {xor_key, blob_hex} from the artifact, confirm
                   candidate ^ key == blob. Pure, no sandbox.
  sandbox_exec   — run the artifact in the sandbox feeding the candidate;
                   success = expected exit code (and optional stdout marker).

Execution is delegated to a runner (default: the real sandbox), so the gate
orders an independent exam without itself touching the host.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Awaitable, Callable

from .contracts import Candidate, SandboxRequest, SandboxResult
from .log import get_logger, kv
from .workspace import resolve_artifact_path

log = get_logger(__name__)

Runner = Callable[[SandboxRequest], Awaitable[SandboxResult]]


def _default_runner() -> Runner:
    from .sandbox import run_sandboxed  # lazy: avoids docker import in pure dev
    return run_sandboxed


class Verifier:
    def __init__(self, runner: Runner | None = None):
        self._runner = runner  # injectable for tests; resolved lazily if None

    async def verify(self, c: Candidate, artifacts: list[str]) -> bool:
        recipe = c.reproduction or {}
        method = recipe.get("method")
        if method == "reencode_xor":
            return self._verify_reencode_xor(c, recipe)
        if method == "sandbox_exec":
            return await self._verify_sandbox_exec(c, recipe)
        log.warning("no verifiable reproduction recipe", extra=kv(
            challenge_id=c.challenge_id, candidate_id=c.id, method=method))
        return False  # fail closed: unverifiable claim is not honored

    # ground truth from the artifact file -----------------------------------
    def _verify_reencode_xor(self, c: Candidate, recipe: dict) -> bool:
        art = recipe.get("artifact")
        try:
            path = resolve_artifact_path(
                art,
                challenge_id=c.challenge_id,
                workdir=c.workdir or None,
            )
            spec = json.loads(path.read_text())
            key, blob_hex = spec["xor_key"], spec["blob_hex"]
        except (OSError, ValueError, KeyError, TypeError):
            return False
        derived = bytes(ord(ch) ^ key for ch in c.candidate).hex()
        ok = derived == blob_hex
        log.info("reencode_xor verification", extra=kv(
            challenge_id=c.challenge_id, verified=ok))
        return ok

    # ground truth from the binary's behavior -------------------------------
    async def _verify_sandbox_exec(self, c: Candidate, recipe: dict) -> bool:
        runner = self._runner or _default_runner()
        res = await runner(SandboxRequest(
            challenge_id=c.challenge_id,
            workdir=c.workdir,
            artifact=recipe.get("artifact", ""),
            argv=recipe.get("argv", []),
            stdin=c.candidate.encode(),
            timeout_s=recipe.get("timeout_s", 15),
        ))
        ok = (res.exit_code == recipe.get("expect_exit", 0)) and not res.timed_out
        marker = recipe.get("success_marker")
        if ok and marker:
            ok = marker.encode() in (res.stdout or b"")
        log.info("sandbox_exec verification", extra=kv(
            challenge_id=c.challenge_id, verified=ok, exit_code=res.exit_code))
        return ok
