"""Runtime configuration. Substrate-agnostic: point these at AEGIS's existing
Kafka/Redis, or run fully local with the in-memory bus and memory backend."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_skills_dir() -> str:
    # runtime/ctfrt/config.py -> runtime/ctfrt -> runtime -> ctf-skill-pack
    return str(Path(__file__).resolve().parents[2])


class Topics:
    """The bus carries only async/durable flows. Synchronous lookups
    (researcher/deepsearcher) are NOT here — they are in-process tools."""
    CHALLENGES = "ctf.challenges"      # new challenges in
    TASKS = "ctf.tasks"               # legacy shared task topic
    HYPOTHESES = "ctf.hypotheses"     # specialists -> orchestrator (dedup/rank)
    CANDIDATES = "ctf.candidates"     # specialists -> flag-discipline gate
    FLAGS = "ctf.flags"               # gate -> orchestrator (verified verdicts)
    HANDOFFS = "ctf.handoffs"         # specialist -> orchestrator (re-route)
    TRACES = "ctf.traces"             # everyone -> append-only log
    SANDBOX_REQ = "ctf.sandbox.requests"
    SANDBOX_RES = "ctf.sandbox.results"

    @staticmethod
    def tasks_for(category: object) -> str:
        value = getattr(category, "value", str(category))
        return f"ctf.tasks.{value}"


@dataclass
class Settings:
    kafka_bootstrap: str = os.getenv("CTF_KAFKA", "localhost:9092")
    redis_url: str = os.getenv("CTF_REDIS", "redis://localhost:6379/0")
    llm_base_url: str = os.getenv("CTF_LLM_BASE", "http://localhost:4000")
    llm_model: str = os.getenv("CTF_LLM_MODEL", "ollama/qwen2.5-coder:32b")
    skills_dir: str = os.getenv("CTF_SKILLS_DIR", default_skills_dir())
    challenge_root: str = os.getenv("CTF_CHALLENGE_ROOT", "/tmp/ctf")
    working_ttl_s: int = int(os.getenv("CTF_WORKING_TTL", str(7 * 24 * 3600)))


settings = Settings()
