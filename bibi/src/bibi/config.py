"""
Bibi configuration.

Locked design:
  - YAML config file (default ~/.bibi/config.yaml)
  - CLI overrides take precedence
  - LLM is opt-in via config OR --llm flag
  - LLM adapter speaks OpenAI-compatible only — vLLM, Ollama,
    LiteLLM, OpenAI all work through the same client; Anthropic
    via LiteLLM, never via direct integration in Bibi
  - Runtime never depends on LLM. LLM is a response-side adapter.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path.home() / ".bibi" / "config.yaml"
DEFAULT_DB_PATH = Path.home() / ".bibi" / "bibi.db"
DEFAULT_IDLE_SECONDS = 300  # 5 minutes


@dataclass(slots=True)
class LLMConfig:
    """OpenAI-compatible LLM adapter config.

    Set enabled=True to use. Bibi works fully without an LLM —
    this section is only consulted by the optional response adapter.
    """
    enabled: bool = False
    provider: str = "openai-compatible"
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "EMPTY"
    model: str = "qwen3.5:0.8b"
    timeout_seconds: float = 30.0
    max_tokens: int = 256

    def resolve_env(self) -> "LLMConfig":
        """Substitute ${VAR} references in api_key with environment values."""
        api_key = self.api_key
        if api_key.startswith("${") and api_key.endswith("}"):
            var_name = api_key[2:-1]
            api_key = os.environ.get(var_name, "")
        return LLMConfig(
            enabled=self.enabled,
            provider=self.provider,
            base_url=self.base_url,
            api_key=api_key,
            model=self.model,
            timeout_seconds=self.timeout_seconds,
            max_tokens=self.max_tokens,
        )


@dataclass(slots=True)
class SessionConfig:
    """Session boundary policy."""
    idle_seconds: int = DEFAULT_IDLE_SECONDS


@dataclass(slots=True)
class StorageConfig:
    """Where Bibi keeps its SQLite database."""
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)


@dataclass(slots=True)
class BibiConfig:
    """Top-level config aggregate."""
    storage: StorageConfig = field(default_factory=StorageConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> "BibiConfig":
        """Load from YAML; missing file yields defaults."""
        path = path or DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()
        with path.open("r") as f:
            raw = yaml.safe_load(f) or {}
        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict[str, Any]) -> "BibiConfig":
        storage_raw = raw.get("storage", {})
        session_raw = raw.get("session", {})
        llm_raw = raw.get("llm", {})

        return cls(
            storage=StorageConfig(
                db_path=Path(storage_raw.get("db_path", DEFAULT_DB_PATH)).expanduser(),
            ),
            session=SessionConfig(
                idle_seconds=int(session_raw.get("idle_seconds", DEFAULT_IDLE_SECONDS)),
            ),
            llm=LLMConfig(
                enabled=bool(llm_raw.get("enabled", False)),
                provider=llm_raw.get("provider", "openai-compatible"),
                base_url=llm_raw.get("base_url", "http://localhost:8000/v1"),
                api_key=llm_raw.get("api_key", "EMPTY"),
                model=llm_raw.get("model", "qwen3.5:0.8b"),
                timeout_seconds=float(llm_raw.get("timeout_seconds", 30.0)),
                max_tokens=int(llm_raw.get("max_tokens", 256)),
            ),
        )

    def with_overrides(
        self,
        *,
        db_path: Path | None = None,
        idle_seconds: int | None = None,
        llm_enabled: bool | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
    ) -> "BibiConfig":
        """Return a copy with the given fields overridden (CLI flag layer)."""
        return BibiConfig(
            storage=StorageConfig(
                db_path=db_path or self.storage.db_path,
            ),
            session=SessionConfig(
                idle_seconds=(
                    idle_seconds if idle_seconds is not None
                    else self.session.idle_seconds
                ),
            ),
            llm=LLMConfig(
                enabled=(
                    llm_enabled if llm_enabled is not None else self.llm.enabled
                ),
                provider=self.llm.provider,
                base_url=llm_base_url or self.llm.base_url,
                api_key=llm_api_key or self.llm.api_key,
                model=llm_model or self.llm.model,
                timeout_seconds=self.llm.timeout_seconds,
                max_tokens=self.llm.max_tokens,
            ),
        )
