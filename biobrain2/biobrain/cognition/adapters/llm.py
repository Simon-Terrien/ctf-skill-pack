"""
biobrain.cognition.adapters.llm — LLM-backed reasoning adapter
=================================================================

Plugs real LLM inference into the cognition layer. Supports:
  - Local Ollama (default, privacy-preserving)
  - OpenAI-compatible APIs (OpenAI, Anthropic via proxy, vLLM)
  - Custom endpoints

Each ReasoningMode gets a mode-specific system prompt that shapes
how the LLM reasons about the task. The executive chooses the mode;
this adapter executes it.

Usage:
    from biobrain.cognition.adapters.llm import LLMReasoner
    from biobrain.cognition import register_reasoner
    from biobrain.core.enums import ReasoningMode

    llm = LLMReasoner(provider="ollama", model="mistral-nemo")
    register_reasoner(ReasoningMode.DIRECT, llm)
    register_reasoner(ReasoningMode.CHECKLIST, llm)
    # ... register for all modes you want LLM-backed

    # Or register for ALL modes at once:
    llm.register_all()
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

from ...core.enums import ReasoningMode, SystemMode
from ...core.signals import ExecutiveDecision, CognitiveResult, ModeState, MemoryItem

logger = logging.getLogger("biobrain.cognition.llm")


# ─── Mode-specific system prompts ────────────────────────────────────────────

SYSTEM_PROMPTS: dict[ReasoningMode, str] = {
    ReasoningMode.DIRECT: (
        "You are a direct, efficient assistant. Give a clear, concise answer. "
        "Do not over-explain. Lead with the answer."
    ),
    ReasoningMode.CHECKLIST: (
        "You are a systematic security analyst. Approach this task as a checklist:\n"
        "1. Identify what needs to be verified\n"
        "2. For each item, state the check, the expected result, and the actual finding\n"
        "3. Flag any failures or deviations\n"
        "4. Summarize: pass/fail/partial\n"
        "If procedural memory (playbooks) are provided, follow them step by step."
    ),
    ReasoningMode.CAUSAL: (
        "You are a root cause analyst. For the given situation:\n"
        "1. Identify the observed problem or symptom\n"
        "2. List possible causes (most likely first)\n"
        "3. For each cause, state what evidence supports or contradicts it\n"
        "4. Identify the most probable root cause\n"
        "5. Recommend corrective action"
    ),
    ReasoningMode.PLANNING: (
        "You are a technical planner. Decompose the task into concrete steps:\n"
        "1. State the goal clearly\n"
        "2. List prerequisites and dependencies\n"
        "3. Break into ordered steps with expected outputs\n"
        "4. Identify risks and fallbacks\n"
        "5. Estimate effort for each step"
    ),
    ReasoningMode.RETRIEVAL: (
        "You are an evidence-grounded analyst. Base your response strictly on "
        "the provided evidence and retrieved memories. For each claim:\n"
        "- Cite the specific evidence that supports it\n"
        "- Flag any claims you cannot ground in evidence\n"
        "- Do not speculate beyond what the evidence supports\n"
        "If evidence is insufficient, say so explicitly."
    ),
    ReasoningMode.CRITIC: (
        "You are a critical reviewer. Your job is to challenge and verify:\n"
        "1. State the claim or assumption being evaluated\n"
        "2. Identify weaknesses, gaps, or contradictions\n"
        "3. Check for logical fallacies or unsupported leaps\n"
        "4. Provide counter-arguments or alternative explanations\n"
        "5. Give a confidence assessment: strong/moderate/weak/unsupported"
    ),
    ReasoningMode.SIMULATION: (
        "You are a scenario analyst. For the given situation:\n"
        "1. Define the current state\n"
        "2. Identify the proposed action or change\n"
        "3. Simulate likely outcomes (best case, worst case, most likely)\n"
        "4. Identify second-order effects\n"
        "5. Recommend: proceed, modify, or abort"
    ),
}

# ─── Mode modifiers: how SystemMode changes the prompt ────────────────────────

MODE_MODIFIERS: dict[SystemMode, str] = {
    SystemMode.AUDIT: (
        "\n\nAUDIT MODE: Every claim must cite evidence. Include provenance "
        "for all facts. Flag any statement that lacks supporting evidence."
    ),
    SystemMode.INCIDENT: (
        "\n\nINCIDENT MODE: Prioritize speed and containment. Lead with "
        "the most critical finding. Keep analysis focused and actionable."
    ),
    SystemMode.RISK: (
        "\n\nRISK MODE: Exercise elevated caution. Flag uncertainties explicitly. "
        "Recommend verification steps before any destructive or irreversible action."
    ),
    SystemMode.LOW_CONFIDENCE: (
        "\n\nLOW CONFIDENCE MODE: Be explicit about uncertainty. State what you "
        "know vs what you're inferring. Recommend escalation for any ambiguous finding."
    ),
}


def _build_user_message(decision: ExecutiveDecision) -> str:
    """Build the user message from the executive decision context."""
    parts = []

    # The original input
    content = decision.salience.perceived.normalized_content
    if content:
        parts.append(f"## Task\n{content}")

    # Memory context
    if decision.memory:
        memory_parts = []
        for label, items in [
            ("Working Memory", decision.memory.working),
            ("Episodic Memory (past interactions)", decision.memory.episodic),
            ("Semantic Memory (knowledge base)", decision.memory.semantic),
            ("Procedural Memory (playbooks/SOPs)", decision.memory.procedural),
        ]:
            if items:
                texts = [m.text[:500] for m in items[:5]]
                memory_parts.append(f"### {label}\n" + "\n---\n".join(texts))

        if decision.memory.kg_facts:
            facts = [str(f) for f in decision.memory.kg_facts[:10]]
            memory_parts.append("### Knowledge Graph Facts\n" + "\n".join(facts))

        if memory_parts:
            parts.append("## Retrieved Context\n" + "\n\n".join(memory_parts))

    # Policy notes
    if decision.policy_notes:
        parts.append("## Policy Notes\n" + "\n".join(f"- {n}" for n in decision.policy_notes))

    # Inhibitions
    if decision.inhibited_actions:
        parts.append(
            "## Inhibited Actions (DO NOT attempt these)\n"
            + "\n".join(f"- {i}" for i in decision.inhibited_actions)
        )

    return "\n\n".join(parts)


class LLMReasoner:
    """LLM-backed reasoner that implements the Reasoner protocol.

    Uses mode-specific system prompts and injects memory context
    into the user message. Supports Ollama and OpenAI-compatible APIs.
    """

    def __init__(
        self,
        provider: str = "ollama",
        model: str = "mistral-nemo",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        timeout_seconds: float = 60.0,
    ):
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key

        if base_url:
            self.base_url = base_url.rstrip("/")
        elif provider == "ollama":
            self.base_url = "http://localhost:11434"
        elif provider == "openai":
            self.base_url = "https://api.openai.com"
        elif provider == "anthropic":
            self.base_url = "https://api.anthropic.com"
        else:
            self.base_url = "http://localhost:11434"

    def run(self, decision: ExecutiveDecision, mode: ModeState) -> CognitiveResult:
        """Execute LLM-backed reasoning for the given decision."""
        reasoning_mode = decision.chosen_reasoning
        start = time.time()

        # Build prompts
        system_prompt = SYSTEM_PROMPTS.get(reasoning_mode, SYSTEM_PROMPTS[ReasoningMode.DIRECT])
        modifier = MODE_MODIFIERS.get(mode.mode, "")
        if modifier:
            system_prompt += modifier

        user_message = _build_user_message(decision)

        trace = [
            f"llm_reasoning: {reasoning_mode.value}",
            f"provider: {self.provider}/{self.model}",
            f"mode: {mode.mode.value}",
        ]

        # Call LLM
        try:
            response_text = self._call_llm(system_prompt, user_message)
            elapsed = time.time() - start
            trace.append(f"llm_response_time: {elapsed:.2f}s")
            trace.append(f"response_length: {len(response_text)} chars")

            # Extract evidence markers if retrieval mode
            evidence = []
            if reasoning_mode == ReasoningMode.RETRIEVAL and decision.memory:
                for item in decision.memory.all_items:
                    if item.text[:50] in response_text or item.text[:30].lower() in response_text.lower():
                        evidence.append(f"[{item.memory_type}] {item.text[:200]}")

            # Confidence heuristic: higher if grounded in evidence
            base_confidence = 0.6
            if evidence:
                base_confidence = min(0.9, 0.5 + 0.1 * len(evidence))
            if mode.mode == SystemMode.LOW_CONFIDENCE:
                base_confidence *= 0.8

            return CognitiveResult(
                decision=decision,
                reasoning_mode_used=reasoning_mode,
                result=response_text,
                evidence=evidence,
                confidence=round(base_confidence, 3),
                reasoning_trace=trace,
            )

        except Exception as e:
            elapsed = time.time() - start
            trace.append(f"llm_error: {e}")
            trace.append(f"llm_error_time: {elapsed:.2f}s")
            logger.error("LLM call failed: %s", e)

            return CognitiveResult(
                decision=decision,
                reasoning_mode_used=reasoning_mode,
                result=f"[LLM ERROR: {e}]",
                confidence=0.0,
                reasoning_trace=trace,
            )

    def _call_llm(self, system_prompt: str, user_message: str) -> str:
        """Call the LLM API. Returns the response text."""
        if self.provider == "ollama":
            return self._call_ollama(system_prompt, user_message)
        else:
            return self._call_openai_compatible(system_prompt, user_message)

    def _call_ollama(self, system_prompt: str, user_message: str) -> str:
        """Call Ollama's /api/chat endpoint."""
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"})

        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("message", {}).get("content", "")
        except URLError as e:
            raise ConnectionError(f"Ollama at {url}: {e}") from e

    def _call_openai_compatible(self, system_prompt: str, user_message: str) -> str:
        """Call OpenAI-compatible /v1/chat/completions endpoint."""
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers=headers)

        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choices = result.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return ""
        except URLError as e:
            raise ConnectionError(f"OpenAI-compatible API at {url}: {e}") from e

    def register_all(self) -> None:
        """Register this LLM reasoner for ALL reasoning modes."""
        from ...cognition import register_reasoner
        for mode in ReasoningMode:
            register_reasoner(mode, self)
        logger.info("LLM reasoner registered for all %d modes: %s/%s",
                    len(ReasoningMode), self.provider, self.model)
