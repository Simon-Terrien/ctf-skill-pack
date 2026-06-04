"""
biobrain.core.enums — All enumeration types
=============================================

Centralized enums used across all modules.
"""

from enum import Enum


class InputSource(Enum):
    """Where a signal came from — determines trust level."""
    USER = "user"
    DOCUMENT = "document"
    TOOL_RESULT = "tool_result"
    API_RESPONSE = "api_response"
    LOG = "log"
    WEB = "web"
    INTERNAL = "internal"


class TrustLevel(Enum):
    """How much to trust this input."""
    VERIFIED = "verified"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    ADVERSARIAL = "adversarial"


class Priority(Enum):
    CRITICAL = 4
    HIGH = 3
    NORMAL = 2
    LOW = 1
    IGNORE = 0


class ReasoningMode(Enum):
    """Which cognitive specialist to invoke."""
    DIRECT = "direct"
    CHECKLIST = "checklist"
    CAUSAL = "causal"
    PLANNING = "planning"
    RETRIEVAL = "retrieval"
    CRITIC = "critic"
    SIMULATION = "simulation"


class SystemMode(Enum):
    """Global modulatory state — changes how ALL modules behave."""
    NORMAL = "normal"
    RISK = "risk"
    LOW_CONFIDENCE = "low_confidence"
    INCIDENT = "incident"
    AUDIT = "audit"
    BUDGET_CONSTRAINED = "budget"
    AUTONOMOUS = "autonomous"
    USER_FACING = "user_facing"


class ActionType(Enum):
    TOOL_CALL = "tool_call"
    API_REQUEST = "api_request"
    REPORT = "report"
    NOTIFICATION = "notification"
    STATE_UPDATE = "state_update"
    WORKFLOW_TRANSITION = "workflow_transition"
    ESCALATION = "escalation"
    NO_ACTION = "no_action"


class ReflexVerdict(Enum):
    """Reflex layer output — fast, deterministic, pre-reasoning."""
    PASS = "pass"
    BLOCK = "block"
    SANITIZE = "sanitize"
    ESCALATE = "escalate"
    ROUTE = "route"


class OperationClass(Enum):
    """Structured operation types for policy enforcement."""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DELETE = "delete"
    ESCALATE = "escalate"
    REPORT = "report"
    CONFIGURE = "configure"
