"""Challenge workspace registration and artifact resolution."""
from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath

from .config import settings


def _safe_component(value: str, *, default: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or default


def normalize_workdir(value: str | None, challenge_id: str) -> str:
    if value:
        return normalize_relative_path(value)
    return _safe_component(challenge_id, default="challenge")


def normalize_relative_path(value: str) -> str:
    if not value:
        raise ValueError("empty artifact path")
    p = PurePosixPath(value.replace("\\", "/"))
    if p.is_absolute():
        raise ValueError(f"absolute paths are not allowed: {value}")
    parts = [part for part in p.parts if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"unsafe relative path: {value}")
    return PurePosixPath(*parts).as_posix()


def workspace_root(challenge_id: str, workdir: str | None = None) -> Path:
    return Path(settings.challenge_root) / normalize_workdir(workdir, challenge_id)


def resolve_artifact_path(
    artifact: str,
    *,
    challenge_id: str,
    workdir: str | None = None,
    strict: bool = True,
) -> Path:
    if workdir:
        base = workspace_root(challenge_id, workdir)
        rel = normalize_relative_path(artifact)
        resolved_base = base.resolve(strict=False)
        resolved_artifact = (base / rel).resolve(strict=strict)
        if not resolved_artifact.is_relative_to(resolved_base):
            raise ValueError(f"artifact escapes workspace: {artifact}")
        return resolved_artifact

    path = Path(artifact)
    if not path.is_absolute():
        raise ValueError(f"artifact path must be relative when no workdir is set: {artifact}")
    return path.resolve(strict=strict)


def register_artifacts(
    challenge_id: str,
    artifacts: list[str],
    *,
    workdir: str | None = None,
) -> tuple[str, list[str]]:
    normalized_workdir = normalize_workdir(workdir, challenge_id)
    root = workspace_root(challenge_id, normalized_workdir)
    root.mkdir(parents=True, exist_ok=True)

    registered: list[str] = []
    used: set[str] = set()
    for idx, artifact in enumerate(artifacts, start=1):
        source = Path(artifact).expanduser()
        try:
            source_resolved = source.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"artifact not found: {artifact}") from exc
        if not source_resolved.is_file():
            raise ValueError(f"artifact is not a regular file: {artifact}")

        stem = _safe_component(source_resolved.stem, default=f"artifact_{idx}")
        suffix = "".join(source_resolved.suffixes)
        candidate = stem + suffix
        serial = 1
        while candidate in used or (root / candidate).exists():
            serial += 1
            candidate = f"{stem}_{serial}{suffix}"
        used.add(candidate)

        target = root / candidate
        shutil.copy2(source_resolved, target)
        registered.append(candidate)

    return normalized_workdir, registered
