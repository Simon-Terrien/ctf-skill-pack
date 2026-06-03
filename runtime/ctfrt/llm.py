"""Provider-abstracted LLM access + SKILL.md loading.

Goes through an OpenAI-compatible endpoint so it works against LiteLLM (your
AEGIS gateway) or a local Ollama/vLLM server with the same code. No vendor
lock-in in the agent layer.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .config import settings


def load_skill(category: str, skills_dir: Optional[str] = None) -> str:
    """Load a category's SKILL.md as the agent's operating instructions.
    `category` is the directory name (e.g. 'reverse', 'crypto-attack')."""
    base = Path(skills_dir or settings.skills_dir).resolve()
    path = base / category / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    # strip frontmatter; the SOP body is the system prompt
    body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)
    return body.strip()


def load_allowed_tools(category: str, skills_dir: Optional[str] = None) -> list[str]:
    base = Path(skills_dir or settings.skills_dir).resolve()
    text = (base / category / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"allowed-tools:\s*(.+)", text)
    return [t.strip() for t in m.group(1).split(",")] if m else []


class LLM:
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        from openai import AsyncOpenAI  # lazy; openai client speaks to LiteLLM too
        self._client = AsyncOpenAI(
            base_url=(base_url or settings.llm_base_url),
            api_key=os.getenv("CTF_LLM_KEY", "not-needed-for-local"),
        )
        self._model = model or settings.llm_model

    async def chat(self, system: str, messages: list[dict], tools: Optional[list] = None):
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, *messages],
            tools=tools or None,
            temperature=0.2,
        )
        return resp.choices[0].message
