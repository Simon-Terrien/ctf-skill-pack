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
import signal

from .bus import make_bus, Bus
from .log import get_logger, setup_logging, kv
from .config import Topics
from .contracts import Category, SandboxResult, TraceEvent
from .gate import Gate
from .engines import engine_for_category
from .memory import make_working_memory, make_long_term_memory
from .orchestrator import Orchestrator
from .agent import SpecialistAgent
from .agency_registry import build_internal_knowledge_agency
from .sandbox import run_sandboxed
from .contracts import SandboxRequest
from .tools import make_researcher
from .trace_recorder import TraceRecorder

log = get_logger(__name__)


async def sandbox_worker(bus: Bus) -> None:
    log.info("sandbox worker started", extra=kv(group="sandbox"))
    log.debug("subscribe topic", extra=kv(topic=Topics.SANDBOX_REQ, group="sandbox"))
    async for raw in bus.subscribe(Topics.SANDBOX_REQ, group="sandbox"):
        req = SandboxRequest.model_validate(raw)
        log.debug("publish topic", extra=kv(
            topic=Topics.TRACES, challenge_id=req.challenge_id, kind="sandbox_request"))
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
        log.debug("publish topic", extra=kv(
            topic=Topics.TRACES, challenge_id=req.challenge_id, kind=kind))
        log.debug("publish topic", extra=kv(
            topic=Topics.SANDBOX_RES, challenge_id=req.challenge_id, request_id=req.id))
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
    log.info("booting runtime component", extra={"ctf": {"component": component}})
    bus = make_bus()
    await bus.start()
    mem = make_working_memory()
    ltm = make_long_term_memory()
    researcher = make_researcher()
    intelligence_svc = build_internal_knowledge_agency()

    tasks: list[asyncio.Task] = []
    for _name, extra in _optional_components(component, bus):
        tasks.append(asyncio.create_task(extra.run()))
    if component in ("all", "orchestrator"):
        tasks.append(asyncio.create_task(Orchestrator(bus, mem, ltm=ltm).run()))
    if component in ("all", "gate"):
        tasks.append(asyncio.create_task(Gate(bus, mem).run()))
    if component in ("all", "sandbox"):
        tasks.append(asyncio.create_task(sandbox_worker(bus)))
    if component in ("all", "specialists"):
        for cat in Category:
            tasks.append(asyncio.create_task(SpecialistAgent(
                cat, bus, mem, None, researcher,
                engine=engine_for_category(cat),
                ltm=ltm,
                intelligence_svc=intelligence_svc).run()))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        log.info("runtime stop requested", extra=kv(component=component))
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    stop_task = asyncio.create_task(stop.wait())
    try:
        done, pending = await asyncio.wait(
            [*tasks, stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop.is_set():
            for task in tasks:
                task.cancel()
        for task in done:
            if task in tasks and not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    raise exc
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        log.info("runtime shutdown", extra=kv(component=component))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bus.stop()
        await mem.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--component", default="all",
                    choices=["all", "orchestrator", "gate", "sandbox", "specialists", "trace-recorder", "memory"])
    args = ap.parse_args()
    try:
        asyncio.run(main(args.component))
    except KeyboardInterrupt:
        raise SystemExit(130)
