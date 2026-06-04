"""
Optional OpenAI-compatible LLM client for Bibi v1.1.

Locked design:
  - Bibi runtime NEVER depends on the LLM.
  - LLM is a pure response-side adapter.
  - Speaks OpenAI's /v1/chat/completions API only.
  - Works against vLLM, Ollama (in OpenAI-compat mode), LiteLLM,
    OpenAI itself, LM Studio — anything that speaks the wire format.
  - Anthropic is reached via LiteLLM, not via direct integration.
    Bibi does not import the Anthropic SDK.

Processing order in chat mode:
  1. User text → CMS runtime files evidence + updates beliefs
  2. Bibi builds the canonical RuntimeStateView
  3. Optional LLM adapter receives the latest turn + a compact
     state summary as system context
  4. LLM returns text; Bibi prints it

If the LLM call fails, Bibi falls back to deterministic mode and
prints the structured turn summary. The runtime state is unaffected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from cms.runtime.state import RuntimeStateView

from bibi.config import LLMConfig


class LLMUnavailableError(RuntimeError):
    """Raised when the configured LLM endpoint can't be reached or fails."""


@dataclass(slots=True)
class LLMResponse:
    """Structured LLM result — text plus diagnostics."""
    text: str
    model: str
    finish_reason: str | None = None


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat client.

    Imports httpx lazily so Bibi works without the [llm] extra installed.
    """

    def __init__(self, config: LLMConfig):
        self._config = config.resolve_env()

    def chat(
        self,
        *,
        user_text: str,
        state: RuntimeStateView,
        system_preamble: str | None = None,
    ) -> LLMResponse:
        """Send one turn to the LLM. Returns the text response.

        State is summarized into a compact JSON blob and included as
        system context. The LLM doesn't see raw evidence — it sees a
        structured summary that the runtime built. If the LLM wants
        deeper detail, that's a Bibi v3 conversation about retrieval.
        """
        try:
            import httpx
        except ImportError as e:
            raise LLMUnavailableError(
                "httpx is required for LLM mode. Install with: pip install bibi[llm]"
            ) from e

        cfg = self._config
        if not cfg.enabled:
            raise LLMUnavailableError("LLM is disabled in config")

        state_summary = _summarize_state(state)
        system_msg = _build_system_message(state_summary, system_preamble)

        payload = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_text},
            ],
            "max_tokens": cfg.max_tokens,
            "temperature": 0.4,  # modest temperature, deterministic-ish
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        }
        url = cfg.base_url.rstrip("/") + "/chat/completions"

        try:
            with httpx.Client(timeout=cfg.timeout_seconds) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            raise LLMUnavailableError(
                f"LLM request failed: {e}"
            ) from e

        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError) as e:
            raise LLMUnavailableError(
                f"unexpected LLM response shape: {data!r}"
            ) from e

        return LLMResponse(text=text, model=cfg.model, finish_reason=finish)


# ── state summarization helpers ─────────────────────────────────────


def _summarize_state(state: RuntimeStateView) -> dict:
    """Build a compact dict describing the current runtime state.

    The LLM gets:
      - active beliefs (global + scoped, deduplicated)
      - tentative beliefs
      - recent evidence count and freshness
      - last few observations as text snippets

    Notably does NOT include:
      - raw evidence records (too noisy for context)
      - confidence/stability internals (LLM doesn't need to weigh)
      - belief metadata (audit detail, not consumer concern)
    """
    def belief_summary(b):
        return {
            "dimension": b.dimension,
            "value": round(b.value, 3),
            "confidence": round(b.confidence, 3),
            "context_key": b.context_key,
            "status": b.status,
        }

    return {
        "active_beliefs_global": [
            belief_summary(b) for b in state.active_beliefs_global
        ],
        "active_beliefs_scoped": [
            belief_summary(b) for b in state.active_beliefs_scoped
        ],
        "tentative_beliefs": [
            belief_summary(b)
            for b in (state.tentative_beliefs_global + state.tentative_beliefs_scoped)
        ],
        "counts": dict(state.counts),
        "freshness_flags": dict(state.freshness_flags),
        "recent_observations": [
            o.raw_text for o in state.recent_observations[:5]
        ],
    }


def _build_system_message(state_summary: dict, preamble: str | None) -> str:
    """Compose the system message. Default preamble is intentionally minimal."""
    default_preamble = (
        "You are Bibi, a note-taking companion. The user is talking through "
        "their day, ideas, and decisions. The structured state below summarizes "
        "what an evidence-grounded runtime has inferred from prior turns — "
        "treat it as background context, not as something to recite. "
        "Respond briefly and supportively. Do not invent facts. If you reference "
        "a belief from the state, use plain language."
    )
    preamble = preamble or default_preamble
    return f"{preamble}\n\n[runtime state]\n{json.dumps(state_summary, indent=2)}"
