"""Small CLI for submitting and locally smoke-testing CTF challenges."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import uuid
from pathlib import Path

from .agent import SpecialistAgent
from .bus import InMemoryBus, make_bus
from .config import Topics
from .contracts import Candidate, Category, Challenge, TraceEvent
from .gate import Gate
from .engines import engine_for_category
from .memory import InMemoryWorkingMemory, make_working_memory
from .orchestrator import Orchestrator
from .trace_recorder import (
    filter_trace_events,
    iter_trace_events,
    latest_trace_run_id,
    mission_trace_summary,
    summarize_trace_event,
    trace_path_for,
    validate_trace_events,
)
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
    run_id = uuid.uuid4().hex[:12]
    trace_dir = Path(os.getenv("CTF_TRACE_DIR", ".ctfrt/traces"))
    trace_dir.mkdir(parents=True, exist_ok=True)
    await bus.start()

    async def local_trace_recorder() -> None:
        async for raw in bus.subscribe(Topics.TRACES, group="cli-trace-recorder"):
            ev = TraceEvent.model_validate(raw)
            payload = dict(ev.payload or {})
            payload.setdefault("run_id", run_id)
            ev = ev.model_copy(update={"payload": payload})
            path = trace_path_for(trace_dir, ev.challenge_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(ev.model_dump_json())
                fh.write("\n")

    # Start core loops in one process. Only the routed specialist is needed.
    cat = _category(args.category) or Category.misc
    loops = [
        asyncio.create_task(local_trace_recorder()),
        asyncio.create_task(Orchestrator(bus, mem).run()),
        asyncio.create_task(Gate(bus, mem).run()),
        asyncio.create_task(SpecialistAgent(cat, bus, mem, None, researcher,
                                            engine=engine_for_category(cat)).run()),
    ]
    await asyncio.sleep(0.05)

    ch = Challenge(
        id=args.name,
        name=args.name,
        category_hint=cat,
        artifacts=args.artifact or [],
        flag_format=args.flag_format,
        remote=args.remote,
        description=args.description or "",
    )
    flag_sub = bus.subscribe(Topics.FLAGS, group="cli-solve-local")
    trace_sub = bus.subscribe(Topics.TRACES, group="cli-solve-local-traces")
    flag_task = asyncio.create_task(flag_sub.__anext__())
    trace_task = asyncio.create_task(trace_sub.__anext__())
    await asyncio.sleep(0.05)
    await bus.publish(Topics.CHALLENGES, ch, key=ch.id)
    try:
        async with asyncio.timeout(args.timeout):
            while True:
                done, _pending = await asyncio.wait(
                    {flag_task, trace_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if flag_task in done:
                    c = Candidate.model_validate(flag_task.result())
                    if c.challenge_id == ch.id:
                        if c.status == "solved":
                            for _ in range(100):
                                events = iter_trace_events(trace_dir, ch.id)
                                kinds = {ev.kind for ev in events}
                                if (
                                    "routed" in kinds
                                    and "solved" in kinds
                                    and ("candidate_accepted" in kinds or "gate_verdict" in kinds)
                                ):
                                    break
                                await asyncio.sleep(0.02)
                            print(c.candidate)
                        else:
                            print(f"not solved: {c.status} {c.candidate}")
                        return
                    flag_task = asyncio.create_task(flag_sub.__anext__())

                if trace_task in done:
                    ev = TraceEvent.model_validate(trace_task.result())
                    if ev.challenge_id == ch.id and ev.kind in {"engine_error", "engine_no_candidate"}:
                        detail = ev.payload.get("error") or ev.payload.get("reasoning") or ev.payload.get("reason", "")
                        print(f"not solved: {ev.kind} {detail}".rstrip())
                        return
                    trace_task = asyncio.create_task(trace_sub.__anext__())
    except TimeoutError:
        print(f"not solved: timeout after {args.timeout:g}s")
        raise SystemExit(1)
    finally:
        flag_task.cancel()
        trace_task.cancel()
        for task in loops:
            task.cancel()
        await asyncio.gather(flag_task, trace_task, *loops, return_exceptions=True)
        await flag_sub.aclose()
        await trace_sub.aclose()
        await mem.close()
        await bus.stop()


def _trace_dir(args) -> Path:
    return Path(args.trace_dir or os.getenv("CTF_TRACE_DIR", ".ctfrt/traces"))


def _selected_trace_run_id(args, trace_dir: Path) -> str | None:
    if getattr(args, "run_id", None):
        return args.run_id
    if getattr(args, "latest", False):
        run_id = latest_trace_run_id(trace_dir, args.challenge_id)
        if run_id is None:
            raise SystemExit(f"no run_id found for {args.challenge_id}")
        return run_id
    return None


def show_trace(args) -> None:
    trace_dir = _trace_dir(args)
    events = iter_trace_events(trace_dir, args.challenge_id)
    events = filter_trace_events(events, run_id=_selected_trace_run_id(args, trace_dir))
    for ev in events:
        print(summarize_trace_event(ev))


def summarize_trace(args) -> None:
    trace_dir = _trace_dir(args)
    events = iter_trace_events(trace_dir, args.challenge_id)
    events = filter_trace_events(events, run_id=_selected_trace_run_id(args, trace_dir))
    if not events:
        raise SystemExit(f"no trace events found for {args.challenge_id}")
    summary = mission_trace_summary(events)
    technique = summary["technique"]
    print(f"Challenge: {summary['challenge_id']}")
    print(f"Status: {summary['status']}")
    print(f"Category: {summary['category']}")
    print(f"Technique: {','.join(technique) if technique else '?'}")
    print(f"Source: {summary['source']}")
    print(f"Engine: {summary['engine']}")
    print(f"Tool calls: {summary['tool_calls']}")
    print(f"Candidates emitted: {summary['candidates_emitted']}")
    print(f"Accepted candidates: {summary['accepted_candidates']}")
    print(f"Rejected candidates: {summary['rejected_candidates']}")
    print(f"Final event: {summary['final_event']}")


def export_trace(args) -> None:
    trace_dir = _trace_dir(args)
    src = trace_path_for(trace_dir, args.challenge_id)
    if not src.exists():
        raise SystemExit(f"no trace file found for {args.challenge_id}: {src}")
    output = Path(args.output or src.with_suffix(".export.jsonl"))
    events = iter_trace_events(trace_dir, args.challenge_id)
    events = filter_trace_events(events, run_id=_selected_trace_run_id(args, trace_dir))
    output.write_text(
        "".join(ev.model_dump_json() + "\n" for ev in events),
        encoding="utf-8",
    )
    print(output)


def validate_trace(args) -> None:
    trace_dir = _trace_dir(args)
    src = trace_path_for(trace_dir, args.challenge_id)
    if not src.exists():
        raise SystemExit(2)
    events = iter_trace_events(trace_dir, args.challenge_id)
    events = filter_trace_events(events, run_id=_selected_trace_run_id(args, trace_dir))
    if not events:
        raise SystemExit(2)
    errors = validate_trace_events(events)
    if errors:
        print(f"TRACE INVALID: {args.challenge_id}")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print(f"TRACE VALID: {args.challenge_id}")


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
    group = p.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true")
    group.add_argument("--run-id")
    p.set_defaults(func=show_trace)

    p = sub.add_parser("summarize-trace")
    p.add_argument("--challenge-id", required=True)
    p.add_argument("--trace-dir")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true")
    group.add_argument("--run-id")
    p.set_defaults(func=summarize_trace)

    p = sub.add_parser("export-trace")
    p.add_argument("--challenge-id", required=True)
    p.add_argument("--trace-dir")
    p.add_argument("--output")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true")
    group.add_argument("--run-id")
    p.set_defaults(func=export_trace)

    p = sub.add_parser("validate-trace")
    p.add_argument("--challenge-id", required=True)
    p.add_argument("--trace-dir")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true")
    group.add_argument("--run-id")
    p.set_defaults(func=validate_trace)

    args = parser.parse_args()
    if inspect.iscoroutinefunction(args.func):
        asyncio.run(args.func(args))
    else:
        args.func(args)


if __name__ == "__main__":
    main()
