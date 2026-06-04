"""
biobrain.config — Unified configuration from YAML + environment
=================================================================

Load BioBrain configuration from a YAML file with environment
variable overrides. Provides a single Settings object used to
initialize BioBrain, Session, and Orchestrator.

Usage:
    from biobrain.config import load_config

    cfg = load_config("biobrain.yaml")
    brain = BioBrain(**cfg.brain_kwargs)

Environment overrides (prefix BIOBRAIN_):
    BIOBRAIN_PALACE_PATH=/data/palace
    BIOBRAIN_MODEL=mistral-nemo
    BIOBRAIN_PROVIDER=ollama
    BIOBRAIN_MODE=audit
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("biobrain.config")


@dataclass
class Settings:
    """Unified BioBrain configuration."""

    # Memory
    palace_path: str = "~/.mempalace/palace"
    kg_path: Optional[str] = None
    playbook_dir: Optional[str] = None

    # Identity
    identity_config: Optional[str] = None
    mempalace_identity: Optional[str] = None

    # LLM (single model fallback)
    llm_provider: str = "ollama"
    llm_model: str = "mistral-nemo"
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2048

    # Multi-model routing (overrides single model when present)
    models: Optional[dict[str, dict[str, Any]]] = None

    # Orchestrator
    max_steps: int = 10
    max_tool_calls: int = 20
    timeout_seconds: float = 300.0
    halt_on_escalation: bool = True

    # Mode
    initial_mode: str = "normal"

    # Audit
    audit_log: Optional[str] = None
    audit_events: bool = True
    audit_traces: bool = True

    # Runtime
    wing: Optional[str] = None
    room: Optional[str] = None

    @property
    def brain_kwargs(self) -> dict[str, Any]:
        """Arguments for BioBrain.__init__()."""
        return {
            "palace_path": os.path.expanduser(self.palace_path),
            "kg_path": self.kg_path,
            "playbook_dir": self.playbook_dir,
            "identity_config": self.identity_config,
            "mempalace_identity": self.mempalace_identity,
        }

    @property
    def llm_kwargs(self) -> dict[str, Any]:
        """Arguments for LLMReasoner.__init__()."""
        kwargs: dict[str, Any] = {
            "provider": self.llm_provider,
            "model": self.llm_model,
            "temperature": self.llm_temperature,
            "max_tokens": self.llm_max_tokens,
        }
        if self.llm_base_url:
            kwargs["base_url"] = self.llm_base_url
        if self.llm_api_key:
            kwargs["api_key"] = self.llm_api_key
        return kwargs

    @property
    def orchestrator_kwargs(self) -> dict[str, Any]:
        """Arguments for Orchestrator.__init__()."""
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "timeout_seconds": self.timeout_seconds,
            "halt_on_escalation": self.halt_on_escalation,
            "wing": self.wing,
            "room": self.room,
        }

    @property
    def router_config(self) -> dict[str, dict[str, Any]]:
        """Model router configuration."""
        if self.models:
            return self.models
        # Fallback: single model for all roles
        return {
            role: {
                "model": self.llm_model,
                "provider": self.llm_provider,
                "temperature": self.llm_temperature,
                "max_tokens": self.llm_max_tokens,
            }
            for role in ["planner", "coder", "critic", "reasoning", "fast", "security"]
        }


def load_config(path: Optional[str] = None) -> Settings:
    """Load configuration from YAML file + environment overrides.

    Priority: environment > YAML > defaults.
    """
    settings = Settings()

    # Load YAML if provided
    if path:
        cfg_path = Path(path)
        if cfg_path.exists():
            try:
                import yaml
                with open(cfg_path) as f:
                    data = yaml.safe_load(f) or {}
                _apply_dict(settings, data)
                logger.info("Loaded config from %s", cfg_path)
            except Exception as e:
                logger.warning("Failed to load config %s: %s", path, e)

    # Environment overrides (BIOBRAIN_ prefix)
    _apply_env(settings)

    return settings


def _apply_dict(settings: Settings, data: dict) -> None:
    """Apply a dict of values to settings."""
    field_names = {f.name for f in settings.__dataclass_fields__.values()}
    for key, value in data.items():
        if key in field_names and value is not None:
            setattr(settings, key, value)


def _apply_env(settings: Settings) -> None:
    """Apply BIOBRAIN_ environment variables as overrides."""
    prefix = "BIOBRAIN_"
    field_map = {f.name.upper(): f.name for f in settings.__dataclass_fields__.values()}

    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        setting_key = env_key[len(prefix):]
        if setting_key in field_map:
            field_name = field_map[setting_key]
            field_type = type(getattr(settings, field_name))

            try:
                if field_type is bool:
                    setattr(settings, field_name, env_val.lower() in ("true", "1", "yes"))
                elif field_type is int:
                    setattr(settings, field_name, int(env_val))
                elif field_type is float:
                    setattr(settings, field_name, float(env_val))
                else:
                    setattr(settings, field_name, env_val)
            except (ValueError, TypeError):
                logger.warning("Invalid env value for %s: %s", env_key, env_val)
