"""
biobrain.runtime.repl — Interactive REPL for the agentic runtime
===================================================================

A rich interactive shell with:
  - Direct tool invocation (/tool, /tools)
  - Live tracing toggle (/trace on|off)
  - Memory inspection (/memory, /working)
  - Session state (/summary, /mode, /approve, /deny)
  - Pipeline inspection (/last, /decision, /reflex)
  - Orchestration (/run goal...)
  - History (/history)
  - Help (/help)

Usage:
    from biobrain.runtime.repl import REPL
    repl = REPL(brain)
    repl.loop()

Or via CLI:
    biobrain session --wing wing_adeo
"""

from __future__ import annotations

import json
import shlex
import sys
import time
import logging
from io import StringIO
from typing import Any, Optional

from ..core.enums import InputSource, SystemMode
from ..core.events import EventBus
from ..ops.tracing import Tracer
from ..ops import HealthMonitor
from .session import Session

logger = logging.getLogger("biobrain.repl")


class REPL:
    """Interactive REPL for BioBrain.

    All commands start with /. Anything else is sent through
    the session as a pipeline turn.
    """

    def __init__(
        self,
        brain,
        wing: Optional[str] = None,
        room: Optional[str] = None,
        enable_tracing: bool = False,
    ):
        self.brain = brain
        self.session = Session(brain, wing=wing, room=room)
        self.tracer = Tracer(stream=sys.stderr if enable_tracing else None, live=enable_tracing)
        self.monitor = HealthMonitor()
        self._tracing_live = enable_tracing
        self._history: list[str] = []

        # Subscribe tracer and monitor
        brain.bus.subscribe(self.tracer.on_event)
        brain.bus.subscribe(self.monitor.on_event)

        # Command registry
        self._commands: dict[str, tuple[callable, str]] = {
            "help":      (self._cmd_help,      "Show available commands"),
            "h":         (self._cmd_help,      "Alias for /help"),
            "quit":      (self._cmd_quit,      "Exit the REPL"),
            "q":         (self._cmd_quit,      "Alias for /quit"),
            "exit":      (self._cmd_quit,      "Alias for /quit"),
            "summary":   (self._cmd_summary,   "Session summary"),
            "mode":      (self._cmd_mode,      "Show or change mode: /mode [name]"),
            "approve":   (self._cmd_approve,   "Approve pending action"),
            "deny":      (self._cmd_deny,      "Deny pending action"),
            "tools":     (self._cmd_tools,     "List registered tools"),
            "tool":      (self._cmd_tool,      "Run a tool: /tool name arg1=val1 arg2=val2"),
            "trace":     (self._cmd_trace,     "Toggle tracing: /trace on|off|show|clear"),
            "timeline":  (self._cmd_timeline,  "Show trace timeline"),
            "last":      (self._cmd_last,      "Inspect last trace"),
            "decision":  (self._cmd_decision,  "Show last executive decision"),
            "reflex":    (self._cmd_reflex,    "Show last reflex verdict"),
            "memory":    (self._cmd_memory,    "Show memory status"),
            "working":   (self._cmd_working,   "Show working memory contents"),
            "health":    (self._cmd_health,    "Show health metrics"),
            "metrics":   (self._cmd_metrics,   "Show full metrics"),
            "run":       (self._cmd_run,       "Orchestrate: /run goal step1, step2..."),
            "history":   (self._cmd_history,   "Show command history"),
            "json":      (self._cmd_json,      "Last trace as JSON"),
            "inhibited": (self._cmd_inhibited, "Show all inhibited actions this session"),
            "clear":     (self._cmd_clear,     "Clear screen"),
        }

    def loop(self) -> None:
        """Main REPL loop."""
        self._print_banner()

        try:
            while True:
                try:
                    line = input(self._prompt()).strip()
                except EOFError:
                    break

                if not line:
                    continue

                self._history.append(line)

                if line.startswith("/"):
                    parts = line[1:].split(None, 1)
                    cmd = parts[0].lower()
                    arg = parts[1] if len(parts) > 1 else ""

                    if cmd in self._commands:
                        fn, _ = self._commands[cmd]
                        result = fn(arg)
                        if result == "QUIT":
                            break
                    else:
                        print(f"  Unknown command: /{cmd}. Type /help for commands.")
                else:
                    self._process_turn(line)

        except KeyboardInterrupt:
            pass

        print(f"\n{self.session.summary}")

    def _prompt(self) -> str:
        mode = self.brain.mode_manager.state.mode.value
        turn = self.session.turn_count
        trace_tag = " 🔍" if self._tracing_live else ""
        return f"[{mode}|t{turn}{trace_tag}] >>> "

    def _print_banner(self) -> None:
        sid = self.session.session_id
        mode = self.brain.mode_manager.state.mode.value
        wing = self.session.wing or "none"
        print(f"╔══════════════════════════════════════════╗")
        print(f"║  BioBrain REPL — Agentic Runtime v0.7   ║")
        print(f"╠══════════════════════════════════════════╣")
        print(f"║  Session:  {sid:28s}  ║")
        print(f"║  Mode:     {mode:28s}  ║")
        print(f"║  Wing:     {wing:28s}  ║")
        print(f"╚══════════════════════════════════════════╝")
        print(f"  Type /help for commands, or just type your input.\n")

    def _process_turn(self, content: str) -> None:
        """Process regular input through the session."""
        trace = self.session.turn(content)
        print(f"  {trace.audit_summary}")

        if trace.halted_at:
            reason = trace.halt_reason or trace.halted_at
            print(f"  ⛔ halted: {reason}")

        if trace.decision and trace.decision.inhibited_actions:
            print(f"  🚫 inhibited: {', '.join(trace.decision.inhibited_actions)}")

        if trace.decision and trace.decision.policy_notes:
            for note in trace.decision.policy_notes:
                print(f"  📋 {note}")

        if trace.cognitive and trace.cognitive.result:
            result_text = trace.cognitive.result
            if len(result_text) > 800:
                result_text = result_text[:800] + "\n  [... truncated, /last for full]"
            print(f"\n{result_text}")

        if trace.action_results:
            for ar in trace.action_results:
                status = "✓" if ar.success else "✗"
                tool_info = f" [{ar.tool_name}]" if ar.tool_name else ""
                print(f"  {status} {ar.request.action_type.value}{tool_info} ({ar.execution_time_ms:.0f}ms)")
                if ar.error:
                    print(f"    error: {ar.error}")

    # ─── Commands ─────────────────────────────────────────────────────────

    def _cmd_help(self, arg: str) -> None:
        print("\n  Commands (prefix with /):\n")
        # Group by category
        categories = {
            "Session":      ["summary", "mode", "approve", "deny", "history"],
            "Tools":        ["tools", "tool"],
            "Tracing":      ["trace", "timeline", "last", "decision", "reflex", "json"],
            "Memory":       ["memory", "working"],
            "Monitoring":   ["health", "metrics", "inhibited"],
            "Orchestration":["run"],
            "System":       ["help", "clear", "quit"],
        }
        seen = set()
        for category, cmds in categories.items():
            print(f"  {category}:")
            for cmd in cmds:
                if cmd in self._commands and cmd not in seen:
                    _, desc = self._commands[cmd]
                    print(f"    /{cmd:12s} {desc}")
                    seen.add(cmd)
            print()

    def _cmd_quit(self, arg: str) -> str:
        return "QUIT"

    def _cmd_summary(self, arg: str) -> None:
        print(f"  {self.session.summary}")
        pending = self.session.pending_approvals
        if pending:
            print(f"  ⏳ {len(pending)} pending approval(s):")
            for a in pending:
                print(f"    [{a.request_id}] {a.action_description}")

    def _cmd_mode(self, arg: str) -> None:
        if not arg:
            state = self.brain.mode_manager.state
            print(f"  mode: {state.mode.value}")
            print(f"  confidence_floor: {state.confidence_floor}")
            print(f"  autonomy_ceiling: {state.autonomy_ceiling}")
            print(f"  risk_level: {state.risk_level}")
            return
        try:
            self.session.set_mode(SystemMode(arg.strip()), "repl_user")
            print(f"  Mode → {arg.strip()}")
        except ValueError:
            modes = ", ".join(m.value for m in SystemMode)
            print(f"  Unknown mode. Available: {modes}")

    def _cmd_approve(self, arg: str) -> None:
        req_id = arg.strip() or None
        if self.session.approve(req_id):
            print("  ✓ Approved.")
        else:
            print("  No pending approvals.")

    def _cmd_deny(self, arg: str) -> None:
        req_id = arg.strip() or None
        if self.session.deny(req_id):
            print("  ✗ Denied.")
        else:
            print("  No pending approvals.")

    def _cmd_tools(self, arg: str) -> None:
        from ..action import list_tools
        tools = list_tools()
        if not tools:
            print("  No tools registered.")
            return
        print(f"\n  {len(tools)} registered tools:\n")
        for t in tools:
            approval = " [approval required]" if t["requires_approval"] else ""
            print(f"    {t['name']:25s} {t['operation']:10s} {t['description']}{approval}")
        print()

    def _cmd_tool(self, arg: str) -> None:
        """Run a tool directly: /tool name key=value key=value"""
        if not arg:
            print("  Usage: /tool <name> [key=value ...]")
            return

        parts = arg.split()
        tool_name = parts[0]
        tool_args = {}
        for part in parts[1:]:
            if "=" in part:
                k, v = part.split("=", 1)
                # Try to parse as JSON for non-string types
                try:
                    tool_args[k] = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    tool_args[k] = v
            else:
                tool_args[part] = True

        from ..action import _tool_registry
        if tool_name not in _tool_registry:
            print(f"  Unknown tool: {tool_name}. Use /tools to list.")
            return

        meta = _tool_registry[tool_name]
        print(f"  Running {tool_name} [{meta.operation_class.value}]...")

        start = time.time()
        try:
            result = meta.fn(**tool_args)
            elapsed = (time.time() - start) * 1000
            print(f"  ✓ Done ({elapsed:.0f}ms)")
            if isinstance(result, dict):
                for k, v in result.items():
                    v_str = str(v)
                    if len(v_str) > 200:
                        v_str = v_str[:200] + "..."
                    print(f"    {k}: {v_str}")
            else:
                print(f"    {str(result)[:500]}")
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            print(f"  ✗ Error ({elapsed:.0f}ms): {e}")

    def _cmd_trace(self, arg: str) -> None:
        arg = arg.strip().lower()
        if arg == "on":
            self._tracing_live = True
            self.tracer._stream = sys.stderr
            self.tracer._live = True
            print("  🔍 Live tracing ON")
        elif arg == "off":
            self._tracing_live = False
            self.tracer._stream = None
            self.tracer._live = False
            print("  Tracing OFF")
        elif arg == "show":
            print(f"  Spans collected: {self.tracer.span_count}")
            print(f"  Live: {'ON' if self._tracing_live else 'OFF'}")
        elif arg == "clear":
            self.tracer.clear()
            print("  Traces cleared.")
        else:
            print("  Usage: /trace on|off|show|clear")

    def _cmd_timeline(self, arg: str) -> None:
        if self.tracer.span_count == 0:
            print("  No spans recorded. Process something first.")
            return
        print(self.tracer.timeline())

    def _cmd_last(self, arg: str) -> None:
        trace = self.session.traces[-1] if self.session.traces else None
        if not trace:
            print("  No traces yet.")
            return

        print(f"\n  Last trace: {trace.audit_summary}")
        print(f"  Elapsed: {trace.elapsed_ms:.1f}ms")

        if trace.perceived:
            p = trace.perceived
            print(f"\n  Perception:")
            print(f"    intent:     {p.intent}")
            print(f"    class:      {p.classification}")
            print(f"    operation:  {p.operation_class.value}")
            print(f"    entities:   {p.entities[:8]}")
            print(f"    risks:      {p.risk_indicators}")

        if trace.salience:
            s = trace.salience
            print(f"\n  Salience:")
            print(f"    priority:   {s.priority.name}")
            print(f"    risk:       {s.risk_score}")
            print(f"    confidence: {s.confidence}")
            print(f"    reasoning:  {s.suggested_reasoning.value}")

        if trace.reflex:
            r = trace.reflex
            print(f"\n  Reflex: {r.verdict.value}")
            if r.rule_triggered:
                print(f"    rule:   {r.rule_triggered}")
                print(f"    reason: {r.reason}")

        if trace.decision:
            d = trace.decision
            print(f"\n  Decision:")
            print(f"    reasoning:  {d.chosen_reasoning.value}")
            print(f"    actions:    {[a.value for a in d.chosen_actions]}")
            if d.inhibited_actions:
                print(f"    inhibited:  {d.inhibited_actions}")
            for note in d.policy_notes:
                print(f"    policy:     {note}")

        if trace.cognitive:
            c = trace.cognitive
            print(f"\n  Cognition:")
            print(f"    mode:       {c.reasoning_mode_used.value}")
            print(f"    confidence: {c.confidence}")
            print(f"    evidence:   {len(c.evidence)} items")
            if c.result:
                preview = c.result[:300]
                print(f"    result:     {preview}")

        if trace.action_results:
            print(f"\n  Actions:")
            for ar in trace.action_results:
                s = "✓" if ar.success else "✗"
                print(f"    {s} {ar.request.action_type.value} [{ar.tool_name or 'n/a'}] {ar.execution_time_ms:.0f}ms")
                if ar.error:
                    print(f"      error: {ar.error}")

        print()

    def _cmd_decision(self, arg: str) -> None:
        trace = self.session.traces[-1] if self.session.traces else None
        if not trace or not trace.decision:
            print("  No decision in last trace.")
            return
        d = trace.decision
        print(f"  Reasoning:  {d.chosen_reasoning.value}")
        print(f"  Actions:    {[a.value for a in d.chosen_actions]}")
        print(f"  Inhibited:  {d.inhibited_actions}")
        for n in d.policy_notes:
            print(f"  Policy:     {n}")

    def _cmd_reflex(self, arg: str) -> None:
        trace = self.session.traces[-1] if self.session.traces else None
        if not trace or not trace.reflex:
            print("  No reflex in last trace.")
            return
        r = trace.reflex
        print(f"  Verdict: {r.verdict.value}")
        print(f"  Rule:    {r.rule_triggered}")
        print(f"  Reason:  {r.reason}")

    def _cmd_memory(self, arg: str) -> None:
        wm = self.brain.memory.working
        print(f"  Working memory: {wm.size} items")
        status = self.brain.memory.wake_up()
        if "error" not in status:
            print(f"  Palace drawers: {status.get('total_drawers', '?')}")
        else:
            print(f"  Palace: {status['error']}")

    def _cmd_working(self, arg: str) -> None:
        wm = self.brain.memory.working
        items = wm.get_recent(20)
        if not items:
            print("  Working memory is empty.")
            return
        print(f"\n  Working memory ({wm.size} items):\n")
        for item in items:
            text = item.text[:100] if hasattr(item, 'text') else str(item)[:100]
            print(f"    {text}")
        print()

    def _cmd_health(self, arg: str) -> None:
        s = self.monitor.status()
        print(f"  Healthy:     {s['healthy']}")
        print(f"  Uptime:      {s['uptime_s']:.0f}s")
        print(f"  Traces:      {s['traces']}")
        print(f"  Error rate:  {s['error_rate']:.2%}")
        print(f"  Avg latency: {s['avg_latency_ms']:.1f}ms")

    def _cmd_metrics(self, arg: str) -> None:
        m = self.monitor.metrics()
        print(json.dumps(m, indent=2, default=str))

    def _cmd_run(self, arg: str) -> None:
        if not arg:
            print("  Usage: /run <goal description>")
            return
        from .orchestrator import Orchestrator
        orch = Orchestrator(self.brain, max_steps=10, wing=self.session.wing)
        print(f"  Orchestrating: {arg[:80]}")
        result = orch.run(arg)
        print(f"  {result.summary}")
        for s in result.steps:
            tag = " 🔄" if s.replanned else ""
            halt = f" ⛔ {s.halt_reason}" if s.halt_reason else ""
            print(f"    step {s.step_number}: {s.observation}{tag}{halt}")

    def _cmd_history(self, arg: str) -> None:
        if not self._history:
            print("  No history.")
            return
        n = int(arg) if arg.strip().isdigit() else 20
        recent = self._history[-n:]
        print(f"\n  Last {len(recent)} commands:\n")
        for i, h in enumerate(recent, 1):
            print(f"    {i:3d}  {h}")
        print()

    def _cmd_json(self, arg: str) -> None:
        trace = self.session.traces[-1] if self.session.traces else None
        if not trace:
            print("  No traces yet.")
            return
        print(json.dumps(trace.to_dict(), default=str, indent=2))

    def _cmd_inhibited(self, arg: str) -> None:
        all_inhibited = []
        for i, trace in enumerate(self.session.traces, 1):
            if trace.decision and trace.decision.inhibited_actions:
                for inh in trace.decision.inhibited_actions:
                    all_inhibited.append((i, inh))
        if not all_inhibited:
            print("  No inhibitions this session.")
            return
        print(f"\n  {len(all_inhibited)} inhibition(s):\n")
        for turn, inh in all_inhibited:
            print(f"    turn {turn}: {inh}")
        print()

    def _cmd_clear(self, arg: str) -> None:
        print("\033[2J\033[H", end="")
