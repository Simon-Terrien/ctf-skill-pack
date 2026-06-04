"""
biobrain.domain.dev_tools — Developer tools for agentic coding
=================================================================

Sandboxed tools for code generation workflows:
  - shell_exec:   Run shell commands in a sandbox (restricted paths, blocked cmds)
  - git_status:   Git status/log/diff (read-only)
  - git_commit:   Git add + commit (write, requires approval in RISK mode)
  - pytest_run:   Run pytest on a path, return structured results
  - file_read:    Read file with line numbers
  - file_write:   Write/overwrite a file (write op, sandboxed path)
  - file_search:  Grep-like search across files
  - code_search:  AST-aware search for functions/classes (Python)

All tools enforce sandbox boundaries:
  - Allowed paths only (configurable)
  - Blocked commands (rm -rf, sudo, etc.)
  - Output truncation
  - Timeout enforcement via ToolMeta

Usage:
    from biobrain.domain.dev_tools import register_dev_tools

    register_dev_tools(sandbox_root="/home/user/project")
"""

from __future__ import annotations

import ast
import logging
import os
import re
import subprocess
import shlex
from pathlib import Path
from typing import Any, Optional

from ..core.enums import OperationClass
from ..action import register_tool

logger = logging.getLogger("biobrain.domain.dev_tools")

# ─── Sandbox configuration ───────────────────────────────────────────────────

_sandbox_root: str = "."
_max_output: int = 10_000  # chars

BLOCKED_COMMANDS = [
    r"rm\s+-rf\s+/",
    r"sudo\s+rm",
    r"mkfs",
    r"dd\s+if=",
    r":(){ :|:& };:",
    r"chmod\s+777\s+/",
    r"curl.*\|\s*sh",
    r"wget.*\|\s*sh",
]

BLOCKED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in BLOCKED_COMMANDS]


def _check_sandbox(path: str) -> str:
    """Resolve and validate path is within sandbox. Returns absolute path."""
    abs_path = os.path.abspath(os.path.join(_sandbox_root, path))
    abs_root = os.path.abspath(_sandbox_root)
    if not abs_path.startswith(abs_root):
        raise PermissionError(f"Path '{path}' escapes sandbox root '{_sandbox_root}'")
    return abs_path


def _check_command(cmd: str) -> None:
    """Check command against blocklist."""
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(cmd):
            raise PermissionError(f"Blocked command pattern detected: {pattern.pattern}")


def _truncate(text: str, max_len: int = 0) -> str:
    limit = max_len or _max_output
    if len(text) > limit:
        return text[:limit] + f"\n\n[TRUNCATED at {limit} chars, total {len(text)}]"
    return text


# ─── Tool implementations ────────────────────────────────────────────────────

def shell_exec(command: str, cwd: Optional[str] = None) -> dict[str, Any]:
    """Execute a shell command inside the sandbox.

    Blocked commands are rejected before execution.
    Output is truncated to prevent context flooding.
    """
    _check_command(command)
    work_dir = _check_sandbox(cwd or ".")

    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, cwd=work_dir,
            env={**os.environ, "HOME": work_dir},
        )
        return {
            "tool": "shell_exec",
            "command": command,
            "cwd": work_dir,
            "returncode": result.returncode,
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr, 2000),
        }
    except subprocess.TimeoutExpired:
        return {"tool": "shell_exec", "command": command, "error": "timeout (30s)"}
    except Exception as e:
        return {"tool": "shell_exec", "command": command, "error": str(e)}


def git_status(cwd: Optional[str] = None) -> dict[str, Any]:
    """Get git status, branch, recent log."""
    work_dir = _check_sandbox(cwd or ".")
    results = {}

    for name, cmd in [
        ("branch", "git branch --show-current"),
        ("status", "git status --short"),
        ("log", "git log --oneline -10"),
    ]:
        try:
            r = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True,
                timeout=10, cwd=work_dir,
            )
            results[name] = r.stdout.strip()
        except Exception as e:
            results[name] = f"error: {e}"

    results["tool"] = "git_status"
    return results


def git_diff(path: Optional[str] = None, cwd: Optional[str] = None) -> dict[str, Any]:
    """Get git diff (staged + unstaged)."""
    work_dir = _check_sandbox(cwd or ".")
    cmd = "git diff"
    if path:
        cmd += f" -- {shlex.quote(path)}"

    try:
        r = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True,
            timeout=10, cwd=work_dir,
        )
        return {
            "tool": "git_diff",
            "diff": _truncate(r.stdout),
            "lines_changed": r.stdout.count("\n"),
        }
    except Exception as e:
        return {"tool": "git_diff", "error": str(e)}


def git_commit(message: str, files: Optional[list[str]] = None, cwd: Optional[str] = None) -> dict[str, Any]:
    """Stage files and commit."""
    work_dir = _check_sandbox(cwd or ".")
    staged = files or ["."]

    try:
        for f in staged:
            subprocess.run(
                ["git", "add", f], capture_output=True, timeout=10, cwd=work_dir,
            )
        r = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, timeout=10, cwd=work_dir,
        )
        return {
            "tool": "git_commit",
            "message": message,
            "files": staged,
            "returncode": r.returncode,
            "output": r.stdout.strip(),
        }
    except Exception as e:
        return {"tool": "git_commit", "error": str(e)}


def pytest_run(path: str = ".", args: str = "-v --tb=short", cwd: Optional[str] = None) -> dict[str, Any]:
    """Run pytest and return structured results."""
    work_dir = _check_sandbox(cwd or ".")
    test_path = _check_sandbox(path)

    cmd = f"python -m pytest {test_path} {args} --no-header -q"
    try:
        r = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True,
            timeout=120, cwd=work_dir,
        )
        output = r.stdout

        # Parse pass/fail counts
        passed = 0
        failed = 0
        errors = 0
        for line in output.split("\n"):
            m = re.search(r"(\d+) passed", line)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+) failed", line)
            if m:
                failed = int(m.group(1))
            m = re.search(r"(\d+) error", line)
            if m:
                errors = int(m.group(1))

        return {
            "tool": "pytest_run",
            "path": path,
            "returncode": r.returncode,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "all_passed": r.returncode == 0,
            "output": _truncate(output),
        }
    except subprocess.TimeoutExpired:
        return {"tool": "pytest_run", "path": path, "error": "timeout (120s)"}
    except Exception as e:
        return {"tool": "pytest_run", "path": path, "error": str(e)}


def file_read(path: str, start_line: int = 1, end_line: Optional[int] = None) -> dict[str, Any]:
    """Read a file with line numbers."""
    abs_path = _check_sandbox(path)

    try:
        with open(abs_path, "r", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        end = end_line or total
        start = max(1, start_line)
        end = min(end, total)

        selected = lines[start - 1:end]
        numbered = "".join(
            f"{i:5d} | {line}" for i, line in enumerate(selected, start=start)
        )

        return {
            "tool": "file_read",
            "path": path,
            "total_lines": total,
            "range": f"{start}-{end}",
            "content": _truncate(numbered),
        }
    except FileNotFoundError:
        return {"tool": "file_read", "path": path, "error": "file not found"}
    except Exception as e:
        return {"tool": "file_read", "path": path, "error": str(e)}


def file_write(path: str, content: str) -> dict[str, Any]:
    """Write content to a file (create or overwrite). Sandbox-enforced."""
    abs_path = _check_sandbox(path)

    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as f:
            f.write(content)
        return {
            "tool": "file_write",
            "path": path,
            "bytes_written": len(content.encode()),
            "lines": content.count("\n") + 1,
        }
    except Exception as e:
        return {"tool": "file_write", "path": path, "error": str(e)}


def file_search(pattern: str, path: str = ".", extensions: str = ".py") -> dict[str, Any]:
    """Grep-like search across files."""
    root = _check_sandbox(path)
    exts = [e.strip() for e in extensions.split(",")]
    matches = []

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return {"tool": "file_search", "error": f"Invalid regex: {e}"}

    try:
        for dirpath, _, filenames in os.walk(root):
            if "__pycache__" in dirpath or ".git" in dirpath:
                continue
            for fname in filenames:
                if not any(fname.endswith(ext) for ext in exts):
                    continue
                fpath = os.path.join(dirpath, fname)
                rel = os.path.relpath(fpath, root)
                try:
                    with open(fpath, "r", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                matches.append({
                                    "file": rel,
                                    "line": i,
                                    "text": line.rstrip()[:200],
                                })
                                if len(matches) >= 100:
                                    break
                except Exception:
                    continue
                if len(matches) >= 100:
                    break

        return {
            "tool": "file_search",
            "pattern": pattern,
            "root": path,
            "total_matches": len(matches),
            "matches": matches,
        }
    except Exception as e:
        return {"tool": "file_search", "error": str(e)}


def code_search(name: str, kind: str = "function", path: str = ".") -> dict[str, Any]:
    """AST-aware search for Python functions, classes, or imports."""
    root = _check_sandbox(path)
    results = []

    for dirpath, _, filenames in os.walk(root):
        if "__pycache__" in dirpath or ".git" in dirpath:
            continue
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, root)
            try:
                with open(fpath, "r") as f:
                    source = f.read()
                tree = ast.parse(source, filename=rel)
                for node in ast.walk(tree):
                    match = False
                    node_kind = ""
                    if kind == "function" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if name.lower() in node.name.lower():
                            match = True
                            node_kind = "function"
                    elif kind == "class" and isinstance(node, ast.ClassDef):
                        if name.lower() in node.name.lower():
                            match = True
                            node_kind = "class"
                    elif kind == "import":
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                if name.lower() in alias.name.lower():
                                    match = True
                                    node_kind = "import"
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and name.lower() in node.module.lower():
                                match = True
                                node_kind = "import"

                    if match:
                        results.append({
                            "file": rel,
                            "line": getattr(node, "lineno", 0),
                            "name": getattr(node, "name", name),
                            "kind": node_kind,
                        })
            except (SyntaxError, Exception):
                continue

            if len(results) >= 50:
                break

    return {
        "tool": "code_search",
        "name": name,
        "kind": kind,
        "total_matches": len(results),
        "results": results,
    }


# ─── Registration ────────────────────────────────────────────────────────────

TOOLS: dict[str, dict[str, Any]] = {
    "shell_exec": {
        "name": "shell_exec", "fn": shell_exec,
        "operation_class": OperationClass.EXECUTE,
        "requires_approval": False, "safe_in_autonomous": False,
        "timeout_seconds": 30.0,
        "description": "Execute shell command in sandbox",
        "arg_schema": {"command": "str"},
    },
    "git_status": {
        "name": "git_status", "fn": git_status,
        "operation_class": OperationClass.READ,
        "description": "Git status, branch, recent log",
    },
    "git_diff": {
        "name": "git_diff", "fn": git_diff,
        "operation_class": OperationClass.READ,
        "description": "Git diff (staged + unstaged)",
    },
    "git_commit": {
        "name": "git_commit", "fn": git_commit,
        "operation_class": OperationClass.WRITE,
        "requires_approval": True,
        "description": "Git add + commit",
        "arg_schema": {"message": "str"},
    },
    "pytest_run": {
        "name": "pytest_run", "fn": pytest_run,
        "operation_class": OperationClass.EXECUTE,
        "timeout_seconds": 120.0,
        "description": "Run pytest with structured result parsing",
    },
    "file_read": {
        "name": "file_read", "fn": file_read,
        "operation_class": OperationClass.READ,
        "description": "Read file with line numbers",
        "arg_schema": {"path": "str"},
    },
    "file_write": {
        "name": "file_write", "fn": file_write,
        "operation_class": OperationClass.WRITE,
        "description": "Write/overwrite file (sandbox-enforced)",
        "arg_schema": {"path": "str", "content": "str"},
    },
    "file_search": {
        "name": "file_search", "fn": file_search,
        "operation_class": OperationClass.READ,
        "description": "Grep-like regex search across files",
        "arg_schema": {"pattern": "str"},
    },
    "code_search": {
        "name": "code_search", "fn": code_search,
        "operation_class": OperationClass.READ,
        "description": "AST-aware search for Python functions/classes/imports",
        "arg_schema": {"name": "str"},
    },
}


def register_dev_tools(sandbox_root: str = ".") -> int:
    """Register all dev tools with the action layer.

    Args:
        sandbox_root: Root directory for file/shell operations. All paths
                      are resolved relative to this and cannot escape it.
    """
    global _sandbox_root
    _sandbox_root = os.path.abspath(sandbox_root)

    for tool_def in TOOLS.values():
        register_tool(**tool_def)

    logger.info("Registered %d dev tools (sandbox: %s)", len(TOOLS), _sandbox_root)
    return len(TOOLS)
