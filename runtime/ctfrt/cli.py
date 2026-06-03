"""Small CLI for submitting and locally smoke-testing CTF challenges."""
from __future__ import annotations

import argparse
import asyncio
import os
import inspect
from pathlib import Path

from .agent import SpecialistAgent
from .bus import InMemoryBus, make_bus
from .config import Topics
from .contracts import Candidate, Category, Challenge
from .gate import Gate
from .memory import InMemoryWorkingMemory, make_working_memory
from .orchestrator import Orchestrator
from .trace_recorder import iter_trace_events, summarize_trace_event, trace_path_for
from .tools import Researcher


def _category(value: str | None) -> Category | None:
    if not value:
        return None
    return Category(value)


async def submit(args) -> None:
    if not os.getenv("CTF_KAFKA") and not args.force_inmemory:
        raise SystemExit(
            "Refusing to submit to the default in-memory bus from a separate process. "
            "Set CTF_KAFKA for distributed runtime, or use solve-local."
        )
    bus = make_bus()
    await bus.start()
    try:
        ch = Challenge(
            name=args.name,
            category_hint=_category(args.category),
            artifacts=args.artifact or [],
            flag_format=args.flag_format,
            remote=args.remote,
            description=args.description or "",
        )
        await bus.publish(Topics.CHALLENGES, ch, key=ch.id)
        print(ch.id)
    finally:
        await bus.stop()


async def solve_local(args) -> None:
    bus = InMemoryBus()
    mem = InMemoryWorkingMemory()
    researcher = Researcher()
    await bus.start()

    # Start core loops in one process. Only the routed specialist is needed.
    cat = _category(args.category) or Category.misc
    loops = [
        asyncio.create_task(Orchestrator(bus, mem).run()),
        asyncio.create_task(Gate(bus, mem).run()),
        asyncio.create_task(SpecialistAgent(cat, bus, mem, None, researcher).run()),
    ]

    ch = Challenge(
        name=args.name,
        category_hint=cat,
        artifacts=args.artifact or [],
        flag_format=args.flag_format,
        remote=args.remote,
        description=args.description or "",
    )
    await bus.publish(Topics.CHALLENGES, ch, key=ch.id)

    try:
        async with asyncio.timeout(args.timeout):
            async for raw in bus.subscribe(Topics.FLAGS, group="cli-solve-local"):
                c = Candidate.model_validate(raw)
                if c.challenge_id == ch.id:
                    if c.status == "solved":
                        print(c.candidate)
                    else:
                        print(f"not solved: {c.status} {c.candidate}")
                    return
    finally:
        for task in loops:
            task.cancel()
        await asyncio.gather(*loops, return_exceptions=True)
        await mem.close()
        await bus.stop()


def _trace_dir(args) -> Path:
    return Path(args.trace_dir or os.getenv("CTF_TRACE_DIR", ".ctfrt/traces"))


def show_trace(args) -> None:
    events = iter_trace_events(_trace_dir(args), args.challenge_id)
    for ev in events:
        print(summarize_trace_event(ev))


def export_trace(args) -> None:
    trace_dir = _trace_dir(args)
    src = trace_path_for(trace_dir, args.challenge_id)
    if not src.exists():
        raise SystemExit(f"no trace file found for {args.challenge_id}: {src}")
    output = Path(args.output or src.with_suffix(".export.jsonl"))
    output.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m ctfrt.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit")
    p.add_argument("--name", required=True)
    p.add_argument("--category", choices=[c.value for c in Category])
    p.add_argument("--artifact", action="append")
    p.add_argument("--flag-format")
    p.add_argument("--remote")
    p.add_argument("--description")
    p.add_argument("--force-inmemory", action="store_true")
    p.set_defaults(func=submit)

    p = sub.add_parser("solve-local")
    p.add_argument("--name", required=True)
    p.add_argument("--category", choices=[c.value for c in Category], default=Category.misc.value)
    p.add_argument("--artifact", action="append")
    p.add_argument("--flag-format")
    p.add_argument("--remote")
    p.add_argument("--description")
    p.add_argument("--timeout", type=float, default=5.0)
    p.set_defaults(func=solve_local)

    p = sub.add_parser("show-trace")
    p.add_argument("--challenge-id", required=True)
    p.add_argument("--trace-dir")
    p.set_defaults(func=show_trace)

    p = sub.add_parser("export-trace")
    p.add_argument("--challenge-id", required=True)
    p.add_argument("--trace-dir")
    p.add_argument("--output")
    p.set_defaults(func=export_trace)

    args = parser.parse_args()
    if inspect.iscoroutinefunction(args.func):
        asyncio.run(args.func(args))
    else:
        args.func(args)


if __name__ == "__main__":
    main()
