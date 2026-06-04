"""
biobrain.action — Tool execution with guards and confirmation enforcement
===========================================================================

The action system now enforces confirmation in RISK mode (blocks, not warns),
validates tool schemas, and categorizes errors.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..core.enums import ActionType, SystemMode, OperationClass
from ..core.signals import ActionRequest, ActionResult, ModeState

logger = logging.getLogger("biobrain.action")

ToolFn = Callable[..., Any]


@dataclass
class ToolMeta:
    """Metadata for a registered tool."""
    name: str
    fn: ToolFn
    operation_class: OperationClass = OperationClass.READ
    requires_approval: bool = False
    safe_in_autonomous: bool = True
    timeout_seconds: float = 30.0
    idempotent: bool = False
    description: str = ""
    arg_schema: Optional[dict[str, str]] = None  # {"arg_name": "type"} for validation


_tool_registry: dict[str, ToolMeta] = {}


def register_tool(
    name: str,
    fn: ToolFn,
    operation_class: OperationClass = OperationClass.READ,
    requires_approval: bool = False,
    safe_in_autonomous: bool = True,
    timeout_seconds: float = 30.0,
    idempotent: bool = False,
    description: str = "",
    arg_schema: Optional[dict[str, str]] = None,
) -> None:
    """Register a tool with metadata."""
    _tool_registry[name] = ToolMeta(
        name=name, fn=fn, operation_class=operation_class,
        requires_approval=requires_approval, safe_in_autonomous=safe_in_autonomous,
        timeout_seconds=timeout_seconds, idempotent=idempotent, description=description,
        arg_schema=arg_schema,
    )
    logger.info("Registered tool: %s [%s]", name, operation_class.value)


def list_tools() -> list[dict[str, Any]]:
    """List registered tools with metadata."""
    return [
        {"name": t.name, "operation": t.operation_class.value,
         "requires_approval": t.requires_approval, "description": t.description}
        for t in _tool_registry.values()
    ]


def execute(request: ActionRequest, mode: Optional[ModeState] = None) -> ActionResult:
    """Execute an action with guards."""
    mode = mode or ModeState()
    start = time.time()

    try:
        if request.action_type == ActionType.TOOL_CALL:
            return _execute_tool(request, mode, start)
        elif request.action_type == ActionType.REPORT:
            return _ok(request, start, {"report": request.cognitive_result.result,
                                         "evidence": request.cognitive_result.evidence})
        elif request.action_type == ActionType.ESCALATION:
            reason = request.parameters.get("reason", "policy")
            logger.warning("ESCALATION: %s", reason)
            return _ok(request, start, {"escalation": True, "reason": reason,
                                         "requires_human_review": True})
        elif request.action_type == ActionType.NO_ACTION:
            return _ok(request, start, "No action required")
        else:
            return _ok(request, start, f"Action {request.action_type.value} acknowledged")
    except Exception as e:
        return _err(request, start, str(e), "unknown")


def _execute_tool(request: ActionRequest, mode: ModeState, start: float) -> ActionResult:
    """Execute a tool with full guard checks."""
    tool_name = request.parameters.get("tool_name", "")
    tool_args = request.parameters.get("tool_args", {})

    if tool_name not in _tool_registry:
        return _err(request, start, f"Unknown tool: {tool_name}", "validation", tool_name)

    meta = _tool_registry[tool_name]

    # Guard: confirmation enforcement in RISK mode (BLOCKS, not warns)
    if mode.mode == SystemMode.RISK and not request.requires_confirmation:
        if meta.operation_class in (OperationClass.WRITE, OperationClass.EXECUTE,
                                     OperationClass.DELETE, OperationClass.CONFIGURE):
            return _err(request, start,
                       f"RISK mode: {meta.operation_class.value} tool '{tool_name}' "
                       f"requires confirmation flag", "policy", tool_name)

    # Guard: approval-required tools
    if meta.requires_approval and not request.requires_confirmation:
        return _err(request, start,
                   f"Tool '{tool_name}' requires approval", "policy", tool_name)

    # Guard: autonomous safety
    if mode.mode == SystemMode.AUTONOMOUS and not meta.safe_in_autonomous:
        return _err(request, start,
                   f"Tool '{tool_name}' not safe in autonomous mode", "policy", tool_name)

    # Guard: argument schema validation
    if meta.arg_schema:
        validation_err = _validate_args(tool_args, meta.arg_schema)
        if validation_err:
            return _err(request, start,
                       f"Argument validation failed for '{tool_name}': {validation_err}",
                       "validation", tool_name)

    # Dry-run mode: return what WOULD happen without executing
    if request.parameters.get("dry_run", False):
        return ActionResult(
            request=request, success=True, tool_name=tool_name,
            output={"dry_run": True, "tool": tool_name, "args": tool_args,
                     "operation": meta.operation_class.value},
            execution_time_ms=(time.time() - start) * 1000,
        )

    # Execute with timeout enforcement
    try:
        output = _run_with_timeout(meta.fn, tool_args, meta.timeout_seconds)
        return _ok(request, start, output, tool_name)
    except TimeoutError:
        return _err(request, start,
                   f"Tool '{tool_name}' exceeded timeout of {meta.timeout_seconds}s",
                   "timeout", tool_name)
    except Exception as e:
        cat = _categorize_error(str(e))
        return _err(request, start, str(e), cat, tool_name)


def _ok(request: ActionRequest, start: float, output: Any, tool_name: str = "") -> ActionResult:
    return ActionResult(
        request=request, success=True, output=output,
        execution_time_ms=(time.time() - start) * 1000, tool_name=tool_name,
    )


def _err(request: ActionRequest, start: float, error: str,
         category: str, tool_name: str = "") -> ActionResult:
    return ActionResult(
        request=request, success=False, error=error, error_category=category,
        execution_time_ms=(time.time() - start) * 1000, tool_name=tool_name,
    )


def _categorize_error(error: str) -> str:
    e = error.lower()
    if any(p in e for p in ("timeout", "timed out")):
        return "timeout"
    if any(p in e for p in ("permission", "denied", "forbidden", "403")):
        return "permission"
    if any(p in e for p in ("not found", "404")):
        return "not_found"
    if any(p in e for p in ("rate limit", "429", "throttle")):
        return "rate_limit"
    if any(p in e for p in ("connection", "network", "503", "502")):
        return "connection"
    return "unknown"


def _run_with_timeout(fn: ToolFn, args: dict[str, Any], timeout: float) -> Any:
    """Run a function with timeout enforcement using threading."""
    import threading

    result_holder: list[Any] = []
    error_holder: list[Exception] = []

    def _target():
        try:
            result_holder.append(fn(**args))
        except Exception as e:
            error_holder.append(e)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        raise TimeoutError(f"Execution exceeded {timeout}s")
    if error_holder:
        raise error_holder[0]
    return result_holder[0] if result_holder else None


def _validate_args(args: dict[str, Any], schema: dict[str, str]) -> Optional[str]:
    """Validate tool arguments against a simple type schema.

    Schema format: {"arg_name": "str|int|float|bool|list|dict"}
    Returns error message or None if valid.
    """
    TYPE_MAP = {
        "str": str, "int": int, "float": (int, float),
        "bool": bool, "list": list, "dict": dict,
    }

    for arg_name, type_name in schema.items():
        if arg_name not in args:
            return f"Missing required argument: {arg_name}"
        expected = TYPE_MAP.get(type_name)
        if expected and not isinstance(args[arg_name], expected):
            return f"Argument '{arg_name}' expected {type_name}, got {type(args[arg_name]).__name__}"

    return None
