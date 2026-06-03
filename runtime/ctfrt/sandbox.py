"""Isolated executor — the exploit-sandbox gate, as a service."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath

from .config import settings
from .contracts import SandboxRequest, SandboxResult

IMAGE = os.getenv("CTF_SANDBOX_IMAGE", "ctf-re-tools:latest")


def _safe_artifact_path(artifact: str) -> bool:
    if not artifact:
        return False
    p = PurePosixPath(artifact.replace("\\", "/"))
    if p.is_absolute():
        return False
    return ".." not in p.parts


def _docker_argv(req: SandboxRequest, workdir: Path) -> list[str]:
    mount = f"{workdir}:/work" + ("" if req.writable else ":ro")
    return [
        "docker", "run", "--rm", "-i",
        "--network", "bridge" if req.network else "none",
        "--cpus", "2", "--memory", "2g", "--pids-limit", "256",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-v", mount, "-w", "/work",
        IMAGE,
        "./" + req.artifact, *req.argv,
    ]


async def run_sandboxed(req: SandboxRequest) -> SandboxResult:
    if not _safe_artifact_path(req.artifact):
        return SandboxResult(
            request_id=req.id,
            exit_code=-126,
            stderr=b"unsafe sandbox artifact path",
        )

    workdir = Path(settings.challenge_root) / req.challenge_id
    workdir.mkdir(parents=True, exist_ok=True)
    try:
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
        return SandboxResult(request_id=req.id, exit_code=proc.returncode or 0,
                             stdout=out, stderr=err)
    except asyncio.TimeoutError:
        proc.kill()
        return SandboxResult(request_id=req.id, exit_code=-1, timed_out=True)
