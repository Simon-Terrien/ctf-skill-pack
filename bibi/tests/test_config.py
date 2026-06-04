"""Tests for BibiConfig — loading, defaults, env interpolation, CLI overrides."""

import os
from pathlib import Path

import pytest

from bibi.config import BibiConfig, LLMConfig


# ── defaults ─────────────────────────────────────────────────────────


class TestDefaults:
    def test_missing_file_yields_defaults(self, tmp_path):
        cfg = BibiConfig.load(tmp_path / "nonexistent.yaml")
        assert cfg.session.idle_seconds == 300
        assert cfg.llm.enabled is False
        assert cfg.llm.provider == "openai-compatible"

    def test_default_llm_disabled(self):
        cfg = BibiConfig()
        assert cfg.llm.enabled is False


# ── load from file ───────────────────────────────────────────────────


class TestLoad:
    def test_load_storage_path(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "storage:\n"
            "  db_path: /tmp/custom.db\n"
        )
        cfg = BibiConfig.load(config_file)
        assert cfg.storage.db_path == Path("/tmp/custom.db")

    def test_load_session_idle(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "session:\n"
            "  idle_seconds: 600\n"
        )
        cfg = BibiConfig.load(config_file)
        assert cfg.session.idle_seconds == 600

    def test_load_llm_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n"
            "  enabled: true\n"
            "  base_url: http://localhost:11434/v1\n"
            "  api_key: ollama\n"
            "  model: qwen3.5:0.8b\n"
        )
        cfg = BibiConfig.load(config_file)
        assert cfg.llm.enabled is True
        assert cfg.llm.base_url == "http://localhost:11434/v1"
        assert cfg.llm.model == "qwen3.5:0.8b"


# ── env interpolation ────────────────────────────────────────────────


class TestEnvInterpolation:
    def test_resolves_dollar_brace_var(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "secret-value")
        llm = LLMConfig(api_key="${MY_API_KEY}")
        resolved = llm.resolve_env()
        assert resolved.api_key == "secret-value"

    def test_unresolved_var_becomes_empty(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        llm = LLMConfig(api_key="${MISSING_VAR}")
        assert llm.resolve_env().api_key == ""

    def test_literal_key_passes_through(self):
        llm = LLMConfig(api_key="literal-key")
        assert llm.resolve_env().api_key == "literal-key"


# ── overrides ────────────────────────────────────────────────────────


class TestOverrides:
    def test_db_path_override(self):
        base = BibiConfig()
        new = base.with_overrides(db_path=Path("/override.db"))
        assert new.storage.db_path == Path("/override.db")

    def test_llm_enabled_override(self):
        base = BibiConfig()
        new = base.with_overrides(llm_enabled=True)
        assert new.llm.enabled is True

    def test_llm_full_override(self):
        base = BibiConfig()
        new = base.with_overrides(
            llm_enabled=True,
            llm_base_url="http://different/v1",
            llm_api_key="different-key",
            llm_model="different-model",
        )
        assert new.llm.base_url == "http://different/v1"
        assert new.llm.api_key == "different-key"
        assert new.llm.model == "different-model"

    def test_unset_overrides_preserve_base(self):
        base = BibiConfig()
        new = base.with_overrides()
        assert new.session.idle_seconds == base.session.idle_seconds
        assert new.llm.enabled == base.llm.enabled
