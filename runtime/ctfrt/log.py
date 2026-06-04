"""Operational logging.

Distinct from TraceEvent: traces are *domain* events on the bus (audit trail for
writeups and memory consolidation); logs are *operational* telemetry for humans
and ops (boot, routing decisions, verdicts, errors, latencies). Don't duplicate
domain state into logs — log the operational shape of what happened.

Env:
  CTF_LOG_LEVEL  (default INFO)
  CTF_LOG_JSON   (truthy -> JSON lines for prod ingestion; else human-readable)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence

_CONFIGURED = False
_FLAG_RE = re.compile(r"[A-Za-z0-9_]+\{[^}\r\n]{1,200}\}")
_REDACTED = "[REDACTED_FLAG]"


def debug_flags_enabled() -> bool:
    return os.getenv("CTF_DEBUG_FLAGS", "").strip().lower() in {"1", "true", "yes", "on"}


def redact_flag(value: str) -> str:
    if debug_flags_enabled():
        return value
    return _FLAG_RE.sub(_REDACTED, value)


def sanitize(value):
    if debug_flags_enabled():
        return value
    if isinstance(value, str):
        return redact_flag(value)
    if isinstance(value, Mapping):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [sanitize(v) for v in value]
    return value


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # structured extras attached via logger.info(..., extra={"ctf": {...}})
        ctf = getattr(record, "ctf", None)
        if ctf:
            payload.update(sanitize(ctf))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str | None = None, json_mode: bool | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or os.getenv("CTF_LOG_LEVEL", "INFO")).upper()
    use_json = json_mode if json_mode is not None else bool(os.getenv("CTF_LOG_JSON"))
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        _JsonFormatter() if use_json
        else logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                               datefmt="%H:%M:%S")
    )
    root = logging.getLogger("ctfrt")
    root.handlers[:] = [handler]
    root.setLevel(lvl)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """`name` is a module path; logger is namespaced under 'ctfrt'."""
    setup_logging()
    short = name.split(".")[-1]
    return logging.getLogger(f"ctfrt.{short}")


def kv(**fields) -> dict:
    """Helper for structured extras: logger.info('msg', extra=kv(challenge_id=..))."""
    return {"ctf": sanitize(fields)}
