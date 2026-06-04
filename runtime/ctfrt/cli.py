"""Small CLI for submitting and locally smoke-testing CTF challenges."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import uuid
from pathlib import Path

from .agent import SpecialistAgent
from .bus import InMemoryBus, make_bus
from .config import Topics
from .contracts import Candidate, Category, Challenge, TraceEvent
from .gate import Gate
from .engines import engine_for_category
from .log import get_logger, kv, sanitize
from .memory import InMemoryWorkingMemory, make_working_memory
from .orchestrator import Orchestrator, route
from .trace_recorder import (
    filter_trace_events,
    iter_trace_events,
    latest_trace_run_id,
    mission_trace_summary,
    summarize_trace_event,
    trace_path_for,
    validate_trace_events,
)
from .tools import make_researcher
from .workspace import register_artifacts, workspace_root

log = get_logger(__name__)
EXIT_SOLVED = 0
EXIT_NOT_SOLVED = 1
EXIT_RUNTIME_ERROR = 2
EXIT_UNSAFE_INPUT = 3


def _json_dump(value: object) -> str:
    return json.dumps(value, default=str)


def _emit(args, plain: str | None = None, payload: dict | list | None = None) -> None:
    if getattr(args, "json", False):
        print(_json_dump(payload if payload is not None else {"message": plain or ""}))
    elif plain is not None:
        print(plain)


def _unsafe_exit(exc: ValueError) -> SystemExit:
    return SystemExit(EXIT_UNSAFE_INPUT)


def _category(value: str | None) -> Category | None:
    if not value:
        return None
    return Category(value)


async def init_workdir(args) -> None:
    challenge_id = args.name or uuid.uuid4().hex[:12]
    try:
        workdir, artifacts = register_artifacts(challenge_id, args.artifact or [])
    except ValueError as exc:
        if getattr(args, "json", False):
            print(_json_dump({"error": str(exc), "exit_code": EXIT_UNSAFE_INPUT}))
        raise _unsafe_exit(exc) from exc
    root = workspace_root(challenge_id, workdir)
    payload = {
        "challenge_id": challenge_id,
        "workdir": workdir,
        "workspace_path": str(root),
        "artifacts": artifacts,
    }
    _emit(args, plain=str(root), payload=payload)


async def inspect_cmd(args) -> None:
    challenge_id = args.name or uuid.uuid4().hex[:12]
    try:
        workdir, artifacts = register_artifacts(challenge_id, args.artifact or [])
    except ValueError as exc:
        if getattr(args, "json", False):
            print(_json_dump({"error": str(exc), "exit_code": EXIT_UNSAFE_INPUT}))
        raise _unsafe_exit(exc) from exc
    ch = Challenge(
        id=challenge_id,
        name=args.name or challenge_id,
        workdir=workdir,
        category_hint=_category(args.category),
        artifacts=artifacts,
        flag_format=args.flag_format,
        remote=args.remote,
        description=args.description or "",
    )
    orch = Orchestrator(InMemoryBus(), InMemoryWorkingMemory())
    triage = await orch.triage(ch)
    routed = route(triage, ch.category_hint)
    payload = {
        "challenge_id": ch.id,
        "workdir": workdir,
        "artifacts": artifacts,
        "triage": triage,
        "category": routed.value,
    }
    if args.json:
        print(_json_dump(payload))
        return
    print(f"Challenge: {ch.id}")
    print(f"Category: {routed.value}")
    print(f"Artifacts: {','.join(artifacts) if artifacts else '-'}")
    print(f"Triage type: {triage.get('type', '?')}")
    print(f"Artifact types: {','.join(triage.get('artifact_types', [])) or '-'}")


async def validate_candidate(args) -> None:
    candidate = Candidate(
        challenge_id=args.challenge_id,
        workdir=args.workdir or "",
        candidate=args.candidate,
        source=args.source,
        flag_format=args.flag_format,
        validation_level=args.validation_level,
        local_validation=args.local_validation,
        oracle_validation=args.oracle_validation,
        evidence=args.evidence or [],
        technique=args.technique or [],
        reproduction=_candidate_reproduction(args),
    )
    gate = Gate(InMemoryBus(), InMemoryWorkingMemory())
    verdict = await gate.evaluate(candidate)
    payload = {
        "challenge_id": verdict.challenge_id,
        "candidate_id": verdict.id,
        "status": verdict.status,
        "validation_level": verdict.validation_level,
        "local_validation": verdict.local_validation,
        "oracle_validation": verdict.oracle_validation,
        "confidence": verdict.confidence.value,
    }
    if args.json:
        print(_json_dump(payload))
    else:
        print(f"{verdict.status} {verdict.validation_level}")
    raise SystemExit(EXIT_SOLVED if verdict.status in {"solved", "locally_verified"} else EXIT_NOT_SOLVED)


def _candidate_reproduction(args) -> dict | None:
    if not args.reproduction_method:
        return None
    recipe: dict[str, object] = {"method": args.reproduction_method}
    if args.reproduction_artifact:
        recipe["artifact"] = args.reproduction_artifact
    if args.reproduction_argv:
        recipe["argv"] = args.reproduction_argv
    if args.expect_exit is not None:
        recipe["expect_exit"] = args.expect_exit
    return recipe


async def submit(args) -> None:
    if not os.getenv("CTF_KAFKA") and not args.force_inmemory:
        message = (
            "Refusing to submit to the default in-memory bus from a separate process. "
            "Set CTF_KAFKA for distributed runtime, or use solve-local."
        )
        if args.json:
            print(_json_dump({"error": message, "exit_code": EXIT_RUNTIME_ERROR}))
        raise SystemExit(EXIT_RUNTIME_ERROR)
    await _submit_impl(args)


async def _submit_impl(args) -> None:
    challenge_id = uuid.uuid4().hex[:12]
    try:
        workdir, artifacts = register_artifacts(challenge_id, args.artifact or [])
    except ValueError as exc:
        if getattr(args, "json", False):
            print(_json_dump({"error": str(exc), "exit_code": EXIT_UNSAFE_INPUT}))
        raise _unsafe_exit(exc) from exc
    log.info("submit challenge prepared", extra=kv(
        challenge_id=challenge_id, workdir=workdir, artifact_count=len(artifacts)))
    bus = make_bus()
    await bus.start()
    try:
        ch = Challenge(
            id=challenge_id,
            name=args.name,
            workdir=workdir,
            category_hint=_category(args.category),
            artifacts=artifacts,
            flag_format=args.flag_format,
            remote=args.remote,
            description=args.description or "",
        )
        log.debug("publish topic", extra=kv(topic=Topics.CHALLENGES, challenge_id=ch.id))
        await bus.publish(Topics.CHALLENGES, ch, key=ch.id)
        _emit(args, plain=ch.id, payload={"challenge_id": ch.id, "workdir": workdir, "artifacts": artifacts})
    finally:
        log.info("submit challenge finished", extra=kv(challenge_id=challenge_id))
        await bus.stop()


async def solve_local(args) -> None:
    bus = InMemoryBus()
    mem = InMemoryWorkingMemory()
    researcher = make_researcher()
    run_id = uuid.uuid4().hex[:12]
    try:
        workdir, artifacts = register_artifacts(args.name, args.artifact or [])
    except ValueError as exc:
        if getattr(args, "json", False):
            print(_json_dump({"error": str(exc), "exit_code": EXIT_UNSAFE_INPUT}))
        raise _unsafe_exit(exc) from exc
    log.info("solve-local prepared", extra=kv(
        challenge_id=args.name, workdir=workdir, artifact_count=len(artifacts), timeout=args.timeout))
    trace_dir = Path(os.getenv("CTF_TRACE_DIR", ".ctfrt/traces"))
    trace_dir.mkdir(parents=True, exist_ok=True)
    await bus.start()

    async def local_trace_recorder() -> None:
        log.debug("subscribe topic", extra=kv(topic=Topics.TRACES, group="cli-trace-recorder"))
        async for raw in bus.subscribe(Topics.TRACES, group="cli-trace-recorder"):
            ev = TraceEvent.model_validate(raw)
            payload = dict(ev.payload or {})
            payload.setdefault("run_id", run_id)
            ev = ev.model_copy(update={"payload": sanitize(payload)})
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
        workdir=workdir,
        category_hint=cat,
        artifacts=artifacts,
        flag_format=args.flag_format,
        remote=args.remote,
        description=args.description or "",
    )
    flag_sub = bus.subscribe(Topics.FLAGS, group="cli-solve-local")
    trace_sub = bus.subscribe(Topics.TRACES, group="cli-solve-local-traces")
    log.debug("subscribe topic", extra=kv(topic=Topics.FLAGS, group="cli-solve-local"))
    log.debug("subscribe topic", extra=kv(topic=Topics.TRACES, group="cli-solve-local-traces"))
    flag_task = asyncio.create_task(flag_sub.__anext__())
    trace_task = asyncio.create_task(trace_sub.__anext__())
    await asyncio.sleep(0.05)
    log.debug("publish topic", extra=kv(topic=Topics.CHALLENGES, challenge_id=ch.id))
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
                            log.info("solve-local solved", extra=kv(
                                challenge_id=ch.id, status=c.status, candidate=c.candidate))
                            _emit(args, plain=c.candidate, payload={
                                "challenge_id": ch.id,
                                "status": c.status,
                                "candidate": c.candidate,
                            })
                        else:
                            log.info("solve-local finished without solved verdict", extra=kv(
                                challenge_id=ch.id, status=c.status, candidate=c.candidate))
                            _emit(args, plain=f"not solved: {c.status} {c.candidate}", payload={
                                "challenge_id": ch.id,
                                "status": c.status,
                                "candidate": c.candidate,
                            })
                        raise SystemExit(EXIT_SOLVED if c.status == "solved" else EXIT_NOT_SOLVED)
                    flag_task = asyncio.create_task(flag_sub.__anext__())

                if trace_task in done:
                    ev = TraceEvent.model_validate(trace_task.result())
                    if ev.challenge_id == ch.id and ev.kind in {"engine_error", "engine_no_candidate"}:
                        detail = ev.payload.get("error") or ev.payload.get("reasoning") or ev.payload.get("reason", "")
                        log.info("solve-local terminal trace", extra=kv(
                            challenge_id=ch.id, kind=ev.kind, detail=str(detail)))
                        _emit(args, plain=f"not solved: {ev.kind} {detail}".rstrip(), payload={
                            "challenge_id": ch.id,
                            "status": ev.kind,
                            "detail": detail,
                        })
                        raise SystemExit(EXIT_NOT_SOLVED)
                    trace_task = asyncio.create_task(trace_sub.__anext__())
    except TimeoutError:
        log.info("solve-local timeout", extra=kv(challenge_id=ch.id, timeout=args.timeout))
        _emit(args, plain=f"not solved: timeout after {args.timeout:g}s", payload={
            "challenge_id": ch.id,
            "status": "timeout",
            "timeout": args.timeout,
        })
        raise SystemExit(EXIT_NOT_SOLVED)
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
        log.info("solve-local shutdown complete", extra=kv(challenge_id=args.name))


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
    if args.json:
        print(_json_dump([ev.model_dump() for ev in events]))
        return
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
    if args.json:
        print(_json_dump(summary))
        return
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
        if args.json:
            print(_json_dump({"challenge_id": args.challenge_id, "valid": False, "errors": ["trace not found"], "exit_code": EXIT_RUNTIME_ERROR}))
        raise SystemExit(EXIT_RUNTIME_ERROR)
    events = iter_trace_events(trace_dir, args.challenge_id)
    events = filter_trace_events(events, run_id=_selected_trace_run_id(args, trace_dir))
    if not events:
        if args.json:
            print(_json_dump({"challenge_id": args.challenge_id, "valid": False, "errors": ["trace not found"], "exit_code": EXIT_RUNTIME_ERROR}))
        raise SystemExit(EXIT_RUNTIME_ERROR)
    errors = validate_trace_events(events)
    if errors:
        if args.json:
            print(_json_dump({"challenge_id": args.challenge_id, "valid": False, "errors": errors, "exit_code": EXIT_NOT_SOLVED}))
        else:
            print(f"TRACE INVALID: {args.challenge_id}")
            for error in errors:
                print(f"- {error}")
        raise SystemExit(EXIT_NOT_SOLVED)
    _emit(args, plain=f"TRACE VALID: {args.challenge_id}", payload={
        "challenge_id": args.challenge_id,
        "valid": True,
    })


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
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=submit)

    p = sub.add_parser("init-workdir")
    p.add_argument("--name")
    p.add_argument("--artifact", action="append")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=init_workdir)

    p = sub.add_parser("inspect")
    p.add_argument("--name")
    p.add_argument("--category", choices=[c.value for c in Category])
    p.add_argument("--artifact", action="append")
    p.add_argument("--flag-format")
    p.add_argument("--remote")
    p.add_argument("--description")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=inspect_cmd)

    p = sub.add_parser("validate-candidate")
    p.add_argument("--challenge-id", required=True)
    p.add_argument("--workdir")
    p.add_argument("--candidate", required=True)
    p.add_argument("--source", default="cli")
    p.add_argument("--flag-format")
    p.add_argument("--validation-level", choices=["observed", "format_ok", "reproduced", "oracle_accepted"], default="observed")
    p.add_argument("--local-validation", choices=["passed", "failed", "not_attempted"], default="not_attempted")
    p.add_argument("--oracle-validation", choices=["passed", "failed", "not_available"], default="not_available")
    p.add_argument("--evidence", action="append")
    p.add_argument("--technique", action="append")
    p.add_argument("--reproduction-method", choices=["reencode_xor", "sandbox_exec"])
    p.add_argument("--reproduction-artifact")
    p.add_argument("--reproduction-argv", action="append")
    p.add_argument("--expect-exit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=validate_candidate)

    p = sub.add_parser("solve-local")
    p.add_argument("--name", required=True)
    p.add_argument("--category", choices=[c.value for c in Category], default=Category.misc.value)
    p.add_argument("--artifact", action="append")
    p.add_argument("--flag-format")
    p.add_argument("--remote")
    p.add_argument("--description")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=solve_local)

    p = sub.add_parser("show-trace")
    p.add_argument("--challenge-id", required=True)
    p.add_argument("--trace-dir")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true")
    group.add_argument("--run-id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=show_trace)

    p = sub.add_parser("summarize-trace")
    p.add_argument("--challenge-id", required=True)
    p.add_argument("--trace-dir")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true")
    group.add_argument("--run-id")
    p.add_argument("--json", action="store_true")
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
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=validate_trace)

    args = parser.parse_args()
    try:
        if inspect.iscoroutinefunction(args.func):
            asyncio.run(args.func(args))
        else:
            args.func(args)
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
