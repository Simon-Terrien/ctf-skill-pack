"""Entrypoint: boot the orchestrator, the gate, the sandbox worker, and one
specialist per category, all on the shared bus. For local dev this runs on the
InMemoryBus in a single process; with CTF_KAFKA set it joins your real cluster
and each component can instead be deployed independently.

    python -m ctfrt.run          # dev, in-memory
    CTF_KAFKA=broker:9092 python -m ctfrt.run --component orchestrator
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os

from .bus import make_bus, Bus
from .log import get_logger, setup_logging, kv
from .config import Topics
from .contracts import Category, SandboxResult, TraceEvent
from .gate import Gate
from .engines import engine_for_category
from .memory import make_working_memory
from .orchestrator import Orchestrator
from .agent import SpecialistAgent
from .sandbox import run_sandboxed
from .contracts import SandboxRequest
from .tools import Researcher, DeepSearcher
from .trace_recorder import TraceRecorder


async def sandbox_worker(bus: Bus) -> None:
    async for raw in bus.subscribe(Topics.SANDBOX_REQ, group="sandbox"):
        req = SandboxRequest.model_validate(raw)
        await bus.publish(Topics.TRACES, TraceEvent(
            challenge_id=req.challenge_id,
            kind="sandbox_request",
            payload={
                "request_id": req.id,
                "artifact": req.artifact,
                "argv": req.argv,
                "network": req.network,
                "timeout_s": req.timeout_s,
                "writable": req.writable,
                "stdin_len": len(req.stdin or b""),
                "stdin_sha256": hashlib.sha256(req.stdin or b"").hexdigest(),
            },
        ))
        res = await run_sandboxed(req)
        payload = {
            "request_id": res.request_id,
            "exit_code": res.exit_code,
            "timed_out": res.timed_out,
            "stdout_len": len(res.stdout),
            "stdout_sha256": hashlib.sha256(res.stdout).hexdigest(),
            "stderr_len": len(res.stderr),
            "stderr_sha256": hashlib.sha256(res.stderr).hexdigest(),
            "artifacts": res.artifacts,
        }
        if res.timed_out:
            kind = "sandbox_timeout"
        elif res.exit_code in (-126, -127):
            kind = "sandbox_denied"
            payload["stderr_preview"] = res.stderr[:120].decode("utf-8", errors="replace")
        else:
            kind = "sandbox_result"
            payload["stderr_preview"] = res.stderr[:120].decode("utf-8", errors="replace")
            payload["stdout_preview"] = res.stdout[:120].decode("utf-8", errors="replace")
        await bus.publish(Topics.TRACES, TraceEvent(
            challenge_id=req.challenge_id,
            kind=kind,
            payload=payload,
        ))
        await bus.publish(Topics.SANDBOX_RES, res, key=req.challenge_id)


def _optional_components(component: str, bus: Bus):
    """Best-effort runtime extensions that stay off the critical path."""
    extras = []
    if component in ("all", "trace-recorder"):
        trace_dir = os.getenv("CTF_TRACE_DIR", ".ctfrt/traces")
        extras.append(("trace-recorder", TraceRecorder(bus, trace_dir=trace_dir)))

    if component in ("all", "memory"):
        mode = os.getenv("CTF_MEMORY_QUERY", "none").strip().lower()
        if mode == "cms":
            try:
                from .cms_cag import CMSMemory, MemoryConsumer
                db_path = os.getenv("CTF_CMS_DB", ".ctfrt/cms.sqlite")
                extras.append(("memory", MemoryConsumer(bus, CMSMemory(db_path=db_path))))
            except Exception as exc:
                log.warning("CMS memory requested but unavailable", extra=kv(
                    component=component, error=repr(exc)))
    return extras


async def main(component: str = "all") -> None:
    setup_logging()
    log = get_logger("run")
    log.info("booting runtime component", extra={"ctf": {"component": component}})
    bus = make_bus()
    await bus.start()
    mem = make_working_memory()
    researcher = Researcher()

    tasks: list = []
    for _name, extra in _optional_components(component, bus):
        tasks.append(extra.run())
    if component in ("all", "orchestrator"):
        tasks.append(Orchestrator(bus, mem).run())
    if component in ("all", "gate"):
        tasks.append(Gate(bus, mem).run())
    if component in ("all", "sandbox"):
        tasks.append(sandbox_worker(bus))
    if component in ("all", "specialists"):
        for cat in Category:
            tasks.append(SpecialistAgent(cat, bus, mem, None, researcher,
                                         engine=engine_for_category(cat)).run())

    try:
        await asyncio.gather(*tasks)
    finally:
        await bus.stop()
        await mem.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--component", default="all",
                    choices=["all", "orchestrator", "gate", "sandbox", "specialists", "trace-recorder", "memory"])
    args = ap.parse_args()
    asyncio.run(main(args.component))
