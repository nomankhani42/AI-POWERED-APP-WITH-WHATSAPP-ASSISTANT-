"""
part3_agent.py — Groq LLaMA Voice Agent
=========================================

Configures an OpenAI Agents SDK ``Agent`` backed by Groq's
**llama-3.3-70b-versatile** (free tier, 128 k context).

Key design choices
------------------
* ``set_default_openai_client`` — every ``Agent`` in this process will
  route through the Groq client unless overridden per-agent.
* ``set_tracing_disabled(True)`` — tracing is turned off because Groq
  does not support the OpenAI trace-upload endpoint.
* ``ModelSettings(temperature=0.7, max_tokens=150)`` — warm but concise
  replies suitable for spoken output.
* ``max_turns=5`` — safety cap so a run-away tool-loop cannot burn quota.
* **Voice-optimised instructions** — no markdown, ≤2 sentences, reply in
  the user's language.
* Two built-in tools: ``get_current_time`` and ``calculate``.

Environment variable:
    GROQ_API_KEY  — from https://console.groq.com/keys

Usage::

    from part3_agent import voice_agent
    # pass voice_agent to SingleAgentVoiceWorkflow(voice_agent)

Run standalone::

    python part3_agent.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agents import (
    Agent,
    ModelSettings,
    Runner,
    function_tool,
    set_default_openai_client,
    set_tracing_disabled,
)

from part1_groq_client import create_groq_client

# ── global wiring ────────────────────────────────────────────────────

# Point every agent to Groq by default.
# 🔴 Learning: global default keeps the demo simple.
# ✅ Production: pass the client per-agent or use a ModelProvider.
_groq_client = create_groq_client()
set_default_openai_client(_groq_client)

# Groq does not accept OpenAI trace uploads → disable to avoid errors.
# 🔴 Learning: disabled for compatibility.
# ✅ Production: enable tracing with an OpenTelemetry collector instead.
set_tracing_disabled(True)


# ── tools ────────────────────────────────────────────────────────────

@function_tool
def get_current_time() -> str:
    """Return the current UTC date-time as a human-readable string.

    The agent can call this tool whenever the user asks "what time is
    it?", "what is today's date?", or similar questions.

    Returns
    -------
    str
        Formatted as ``"Wednesday, 12 March 2026 14:30 UTC"``.

    Notes
    -----
    • No parameters — the tool always returns UTC.

    # 🔴 Learning: UTC only; user's local TZ is not detected.
    # ✅ Production: accept an optional ``timezone`` param or
    #    detect from the user profile / WhatsApp metadata.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%A, %d %B %Y %H:%M UTC")


@function_tool
def calculate(expression: str) -> str:
    """Evaluate a simple mathematical expression and return the result.

    Parameters
    ----------
    expression : str
        A Python-safe math expression such as ``"2 + 2"``,
        ``"sqrt(144)"``, or ``"3 * (4 + 5)"``.

        • **Accepted values:** strings containing digits and
          ``+ - * / ** ( ) sqrt abs round``.
        • **When to change:** never — the agent formulates the
          expression from the user's question automatically.

        # 🔴 Learning: uses ``eval`` with a restricted namespace.
        # ✅ Production: use a proper math parser (``simpleeval``,
        #    ``asteval``) to eliminate code-injection risk.

    Returns
    -------
    str
        The result as a string, e.g. ``"42.0"``.
        Returns an error message (not an exception) so the agent can
        gracefully report the issue to the user.
    """
    import math as _math

    allowed = {
        "__builtins__": {},
        "sqrt": _math.sqrt,
        "abs": abs,
        "round": round,
        "pi": _math.pi,
        "e": _math.e,
    }
    try:
        # 🔴 Learning: eval with restricted globals is quick & dirty.
        # ✅ Production: replace with simpleeval or a sandboxed runner.
        result = eval(expression, allowed)  # noqa: S307
        return str(result)
    except Exception as exc:
        return f"Could not evaluate '{expression}': {exc}"


# ── voice-optimised instructions ─────────────────────────────────────

VOICE_INSTRUCTIONS: str = (
    "You are a helpful voice assistant. "
    "Your responses will be spoken aloud, so follow these rules strictly:\n"
    "1. Never use markdown, bullet points, numbered lists, or special formatting.\n"
    "2. Keep every reply under two sentences.\n"
    "3. Reply in the same language the user speaks.\n"
    "4. Be conversational and natural — as if chatting with a friend.\n"
    "5. If you don't know something, say so briefly.\n"
    "6. Use the get_current_time tool when the user asks about time or date.\n"
    "7. Use the calculate tool when the user asks a math question."
)
"""System prompt injected into the agent.

• **When to change:** customise personality, restrict topics,
  or add domain knowledge.
"""


# ── agent definition ─────────────────────────────────────────────────

voice_agent: Agent = Agent(
    name="Voice Assistant",
    instructions=VOICE_INSTRUCTIONS,
    model="llama-3.3-70b-versatile",
    model_settings=ModelSettings(
        temperature=0.7,
        # temperature : float
        #   Controls response randomness.
        #   • 0.0 → deterministic, repetitive
        #   • 0.7 → warm & varied  ← good for conversation
        #   • 1.0 → very creative but may hallucinate
        #   Default: 0.7
        #   When to change: lower (0.2) for factual Q&A;
        #   raise (0.9) for creative storytelling.
        max_tokens=150,
        # max_tokens : int
        #   Maximum tokens in the reply.
        #   • 50  → very short (one sentence)
        #   • 150 → 1-2 spoken sentences  ← sweet spot for voice
        #   • 500 → long paragraph (bad UX for voice)
        #   Default: 150
        #   When to change: raise for detailed explanations;
        #   lower for ultra-snappy replies (games, commands).
        #
        # 🔴 Learning: 150 tokens ≈ 8 s of speech.
        # ✅ Production: tune per use-case and measure TTS latency.
    ),
    tools=[get_current_time, calculate],
)
"""Pre-configured voice agent instance.

Import this directly::

    from part3_agent import voice_agent

The agent uses ``max_turns=5`` at *run-time* (set when calling
``Runner.run``).  This prevents infinite tool-call loops from
burning your free-tier quota.
"""


# ── self-test ────────────────────────────────────────────────────────

async def _self_test() -> None:
    """Send a test message to the agent and print the reply."""
    test_messages = [
        "What time is it right now?",
        "What is the square root of 256 plus 10?",
        "Tell me a fun fact.",
    ]

    for msg in test_messages:
        print(f"\n🗣️  User: {msg}")
        result = await Runner.run(
            voice_agent,
            input=msg,
            max_turns=5,
            # max_turns : int
            #   Safety cap on agent ↔ tool round-trips.
            #   • 1  → no tool use (agent answers directly)
            #   • 5  → up to 5 loops (default, good balance)
            #   • 20 → complex multi-step tasks
            #   When to change: increase for workflows that chain
            #   many tool calls; decrease to conserve quota.
            #
            # 🔴 Learning: 5 is generous for a voice demo.
            # ✅ Production: set based on your tool graph depth.
        )
        print(f"🤖  Agent: {result.final_output}")


if __name__ == "__main__":
    asyncio.run(_self_test())
