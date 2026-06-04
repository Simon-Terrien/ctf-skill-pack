"""Isolated executor — the exploit-sandbox gate, as a service."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .config import settings
from .contracts import SandboxRequest, SandboxResult
from .log import get_logger, kv
from .workspace import normalize_relative_path, resolve_artifact_path, workspace_root

IMAGE = os.getenv("CTF_SANDBOX_IMAGE", "ctf-re-tools:latest")
READ_ONLY_ROOT = os.getenv("CTF_SANDBOX_READ_ONLY_ROOT", "1").strip().lower() not in {"0", "false", "no", "off"}
CPU_LIMIT = os.getenv("CTF_SANDBOX_CPUS", "2")
MEMORY_LIMIT = os.getenv("CTF_SANDBOX_MEMORY", "2g")
PIDS_LIMIT = os.getenv("CTF_SANDBOX_PIDS_LIMIT", "256")
FILE_SIZE_LIMIT = os.getenv("CTF_SANDBOX_FILE_SIZE", "10485760")
SEC_COMP = os.getenv("CTF_SANDBOX_SECCOMP", "").strip()
APPARMOR = os.getenv("CTF_SANDBOX_APPARMOR", "").strip()
log = get_logger(__name__)


def _safe_artifact_path(artifact: str) -> bool:
    try:
        normalize_relative_path(artifact)
        return True
    except ValueError:
        return False


def _docker_argv(req: SandboxRequest, workdir: Path) -> list[str]:
    mount = f"{workdir}:/work" + ("" if req.writable else ":ro")
    argv = [
        "docker", "run", "--rm", "-i",
        "--network", "bridge" if req.network else "none",
        "--cpus", CPU_LIMIT, "--memory", MEMORY_LIMIT, "--pids-limit", PIDS_LIMIT,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--ulimit", f"fsize={FILE_SIZE_LIMIT}",
        "-v", mount, "-w", "/work",
    ]
    if READ_ONLY_ROOT:
        argv.extend(["--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"])
    if SEC_COMP:
        argv.extend(["--security-opt", f"seccomp={SEC_COMP}"])
    if APPARMOR:
        argv.extend(["--security-opt", f"apparmor={APPARMOR}"])
    argv.extend([IMAGE, "./" + req.artifact, *req.argv])
    return argv


async def run_sandboxed(req: SandboxRequest) -> SandboxResult:
    if not _safe_artifact_path(req.artifact):
        return SandboxResult(
            request_id=req.id,
            exit_code=-126,
            stderr=b"unsafe sandbox artifact path",
        )

    workdir = workspace_root(req.challenge_id, req.workdir or None)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        resolve_artifact_path(
            req.artifact,
            challenge_id=req.challenge_id,
            workdir=req.workdir or None,
        )
    except ValueError:
        return SandboxResult(
            request_id=req.id,
            exit_code=-126,
            stderr=b"unsafe sandbox artifact path",
        )
    try:
        log.info("sandbox exec start", extra=kv(
            challenge_id=req.challenge_id,
            artifact=req.artifact,
            network=req.network,
            writable=req.writable,
            timeout_s=req.timeout_s))
        proc = await asyncio.create_subprocess_exec(
            *_docker_argv(req, workdir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        return SandboxResult(request_id=req.id, exit_code=-127, stderr=repr(e).encode())

    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=req.stdin), timeout=req.timeout_s
        )
        log.info("sandbox exec finished", extra=kv(
            challenge_id=req.challenge_id, artifact=req.artifact, exit_code=proc.returncode or 0))
        return SandboxResult(request_id=req.id, exit_code=proc.returncode or 0,
                             stdout=out, stderr=err)
    except asyncio.TimeoutError:
        log.warning("sandbox exec timed out", extra=kv(
            challenge_id=req.challenge_id, artifact=req.artifact, timeout_s=req.timeout_s))
        proc.kill()
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:
            log.warning("sandbox exec cleanup timed out", extra=kv(
                challenge_id=req.challenge_id, artifact=req.artifact))
        return SandboxResult(request_id=req.id, exit_code=-1, timed_out=True)
