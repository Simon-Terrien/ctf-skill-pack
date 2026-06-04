"""
biobrain.cli — Command-line interface
=======================================

Usage:
    python -m biobrain run "scan the auth endpoint"
    python -m biobrain session --wing wing_adeo
    python -m biobrain audit /var/log/biobrain/audit.jsonl
    python -m biobrain status --palace ~/.mempalace/palace

Environment:
    BIOBRAIN_PALACE_PATH, BIOBRAIN_MODEL, BIOBRAIN_PROVIDER, etc.
"""

from __future__ import annotations

import argparse
import json
import sys
import logging
from typing import Optional

from .config import load_config, Settings
from .core.enums import InputSource, SystemMode


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="biobrain",
        description="BioBrain — biologically-inspired cognitive runtime",
    )
    parser.add_argument("-c", "--config", help="Path to YAML config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    sub = parser.add_subparsers(dest="command")

    # ── run ────────────────────────────────────────────────────────────────
    run_p = sub.add_parser("run", help="Process a single input")
    run_p.add_argument("input", help="Input text to process")
    run_p.add_argument("--source", default="user", help="Input source (user/web/tool_result/api_response)")
    run_p.add_argument("--mode", default=None, help="System mode (normal/risk/audit/incident)")
    run_p.add_argument("--wing", default=None, help="MemPalace wing")
    run_p.add_argument("--json", action="store_true", help="Output trace as JSON")

    # ── session ───────────────────────────────────────────────────────────
    sess_p = sub.add_parser("session", help="Interactive multi-turn session")
    sess_p.add_argument("--wing", default=None, help="MemPalace wing")
    sess_p.add_argument("--room", default=None, help="MemPalace room")
    sess_p.add_argument("--mode", default=None, help="Initial system mode")

    # ── orchestrate ───────────────────────────────────────────────────────
    orch_p = sub.add_parser("orchestrate", help="Run a multi-step goal")
    orch_p.add_argument("goal", help="Goal to decompose and execute")
    orch_p.add_argument("--max-steps", type=int, default=None)
    orch_p.add_argument("--wing", default=None)
    orch_p.add_argument("--json", action="store_true")

    # ── status ────────────────────────────────────────────────────────────
    sub.add_parser("status", help="Show BioBrain and MemPalace status")

    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    cfg = load_config(args.config)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "run":
        return _cmd_run(cfg, args)
    elif args.command == "session":
        return _cmd_session(cfg, args)
    elif args.command == "orchestrate":
        return _cmd_orchestrate(cfg, args)
    elif args.command == "status":
        return _cmd_status(cfg, args)

    return 0


def _make_brain(cfg: Settings):
    from .runtime.pipeline import BioBrain
    from .core.events import EventBus
    from .core.audit import AuditLogger

    bus = EventBus()
    brain = BioBrain(**cfg.brain_kwargs, event_bus=bus)

    # Attach audit logger if configured
    if cfg.audit_log:
        audit = AuditLogger(
            output=cfg.audit_log,
            include_events=cfg.audit_events,
            include_traces=cfg.audit_traces,
        )
        bus.subscribe(audit.on_event)

    # Set initial mode
    if cfg.initial_mode != "normal":
        try:
            mode = SystemMode(cfg.initial_mode)
            brain.mode_manager.transition(mode, "config")
        except ValueError:
            pass

    return brain


def _cmd_run(cfg: Settings, args) -> int:
    brain = _make_brain(cfg)

    # Parse source
    try:
        source = InputSource(args.source)
    except ValueError:
        source = InputSource.USER

    # Set mode if specified
    if args.mode:
        try:
            brain.mode_manager.transition(SystemMode(args.mode), "cli")
        except ValueError:
            pass

    metadata = {}
    if args.wing:
        metadata["wing"] = args.wing

    trace = brain.process(args.input, source=source, metadata=metadata)

    if args.json:
        print(json.dumps(trace.to_dict(), default=str, indent=2))
    else:
        print(trace.audit_summary)
        if trace.halted_at:
            print(f"  halted: {trace.halted_at} — {trace.halt_reason}")
        if trace.decision and trace.decision.inhibited_actions:
            print(f"  inhibited: {trace.decision.inhibited_actions}")
        if trace.cognitive and trace.cognitive.result:
            print(f"  result: {trace.cognitive.result[:500]}")

    return 0


def _cmd_session(cfg: Settings, args) -> int:
    from .runtime.repl import REPL

    brain = _make_brain(cfg)
    repl = REPL(
        brain,
        wing=args.wing or cfg.wing,
        room=args.room or cfg.room,
        enable_tracing=False,
    )

    if args.mode:
        try:
            repl.session.set_mode(SystemMode(args.mode), "cli")
        except ValueError:
            pass

    repl.loop()
    return 0


def _cmd_orchestrate(cfg: Settings, args) -> int:
    from .runtime.orchestrator import Orchestrator

    brain = _make_brain(cfg)
    orch_kwargs = cfg.orchestrator_kwargs
    if args.max_steps:
        orch_kwargs["max_steps"] = args.max_steps
    if args.wing:
        orch_kwargs["wing"] = args.wing

    orch = Orchestrator(brain, **orch_kwargs)
    result = orch.run(args.goal)

    if args.json:
        output = {
            "goal": result.goal,
            "completed": result.completed,
            "halt_reason": result.halt_reason,
            "total_steps": result.total_steps,
            "total_tool_calls": result.total_tool_calls,
            "total_replans": result.total_replans,
            "elapsed_ms": result.total_elapsed_ms,
            "steps": [
                {
                    "step": s.step_number,
                    "observation": s.observation,
                    "replanned": s.replanned,
                    "halted": s.halt_reason,
                }
                for s in result.steps
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print(result.summary)
        for s in result.steps:
            tag = " [replanned]" if s.replanned else ""
            print(f"  step {s.step_number}: {s.observation}{tag}")

    return 0


def _cmd_status(cfg: Settings, args) -> int:
    brain = _make_brain(cfg)
    status = brain.memory.wake_up()
    print(f"BioBrain v0.7.0")
    print(f"Palace: {cfg.palace_path}")
    print(f"Mode: {brain.mode_manager.state.mode.value}")
    if "error" in status:
        print(f"Memory: {status['error']}")
    else:
        print(f"Memory: {status.get('total_drawers', '?')} drawers")
    print(f"LLM: {cfg.llm_provider}/{cfg.llm_model}")
    return 0
