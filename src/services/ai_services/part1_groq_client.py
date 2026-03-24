"""
part1_groq_client.py — Groq AsyncOpenAI Client
================================================

Creates an ``AsyncOpenAI`` instance whose ``base_url`` points to Groq's
OpenAI-compatible endpoint so every subsequent call (Whisper STT, LLaMA
chat-completions) goes through Groq's free-tier LPU infrastructure.

Why Groq?
---------
Groq provides an OpenAI-compatible REST API backed by custom LPU
(Language Processing Unit) hardware.  Because the SDK is compatible,
you only need to swap ``base_url`` and ``api_key`` — no other code
changes are necessary.

Free-tier limits (no credit card):
    • Chat   — 6 000 tokens / min  (llama-3.3-70b-versatile)
    • Whisper — 20 req / min, 7 200 audio-sec / hour
    • TTS    — currently paid; we use Azure TTS instead

Environment variable required:
    GROQ_API_KEY  — obtain from https://console.groq.com/keys

Usage::

    from part1_groq_client import create_groq_client
    client = create_groq_client()

Run standalone::

    python part1_groq_client.py
"""

from __future__ import annotations

import asyncio
import os

from openai import AsyncOpenAI


# ── factory ──────────────────────────────────────────────────────────

def create_groq_client(
    api_key: str | None = None,
    base_url: str = "https://api.groq.com/openai/v1",
    timeout: float = 30.0,
    max_retries: int = 2,
) -> AsyncOpenAI:
    """Return an **async** OpenAI-compatible client that targets Groq.

    Parameters
    ----------
    api_key : str | None
        Groq API key.  When ``None`` the value of the
        ``GROQ_API_KEY`` environment variable is used.

        • **Accepted values:** any valid Groq key string (starts with
          ``gsk_…``).
        • **Default:** ``None`` → reads ``os.environ.get("GROQ_API_KEY")``.
        • **When to change:** pass explicitly when running unit tests
          with a separate test key, or in multi-tenant setups where
          each tenant has its own key.

    base_url : str
        Root URL of the Groq REST API.

        • **Accepted values:** any HTTPS URL.
        • **Default:** ``"https://api.groq.com/openai/v1"``.
        • **When to change:** only if Groq changes their API path,
          or you route through a corporate proxy / load-balancer.

        # 🔴 Learning: hard-coded default is fine for tutorials.
        # ✅ Production: inject via env var / config to support
        #    proxy rotation or region-specific endpoints.

    timeout : float
        Maximum seconds the HTTP client waits for a response
        before raising ``httpx.TimeoutException``.

        • **Accepted values:** positive float.
        • **Default:** ``30.0``.
        • **When to change:** lower (10–15 s) for real-time voice to
          avoid noticeable pauses; raise (60+ s) for long audio
          transcriptions.

        # 🔴 Learning: 30 s is generous & safe for experiments.
        # ✅ Production: use 10–15 s for voice; add a circuit-breaker.

    max_retries : int
        How many automatic retries the SDK performs on transient
        HTTP errors (503 Service Unavailable, network timeouts).

        • **Accepted values:** non-negative int.
        • **Default:** ``2``.
        • **When to change:** set ``0`` while debugging to see raw
          errors immediately; increase to ``3``–``4`` for unattended
          batch jobs.

    Returns
    -------
    AsyncOpenAI
        Fully configured client.  Use it for:
        ``client.audio.transcriptions.create(…)`` — Whisper STT
        ``client.chat.completions.create(…)``     — LLaMA chat

    Raises
    ------
    openai.AuthenticationError
        If the API key is missing or invalid.

    Examples
    --------
    >>> client = create_groq_client()
    >>> # use with Whisper
    >>> result = await client.audio.transcriptions.create(
    ...     model="whisper-large-v3-turbo", file=wav_bytes
    ... )
    """
    resolved_key = api_key or os.environ.get("GROQ_API_KEY")
    if not resolved_key:
        raise EnvironmentError(
            "GROQ_API_KEY is not set.  "
            "Get a free key at https://console.groq.com/keys "
            "and export it:  export GROQ_API_KEY='gsk_…'"
        )

    # 🔴 Learning: single global client is fine.
    # ✅ Production: use a connection-pool or pass `http_client`
    #    with custom `httpx.AsyncClient(limits=…)` for concurrency.
    return AsyncOpenAI(
        api_key=resolved_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
    )


# ── self-test ────────────────────────────────────────────────────────

async def _self_test() -> None:
    """Quick smoke-test: list available Groq models."""
    client = create_groq_client()
    models = await client.models.list()
    print("✅  Groq client OK — available models:")
    for m in models.data[:10]:
        print(f"   • {m.id}")
    if len(models.data) > 10:
        print(f"   … and {len(models.data) - 10} more")


if __name__ == "__main__":
    asyncio.run(_self_test())
