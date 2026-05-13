"""OpenAI GPT-4o Mini Text-to-Speech service.

Replaces Azure TTS with OpenAI's gpt-4o-mini-tts model.
Output: raw PCM 24 kHz, 16-bit, mono — same spec as the old Azure service.

Public API
----------
* ``synthesise_speech``      — async generator yielding PCM chunks
* ``synthesise_to_bytes``    — collect all chunks into a single bytes object
* ``synthesise_to_wav_b64``  — convenience: PCM → WAV → base64 string
* ``create_tts_model``       — factory returning a TTSModel for VoicePipeline

Environment variable:
    OPENAI_API_KEY — from https://platform.openai.com/api-keys
"""

from __future__ import annotations

import asyncio
import base64
import io
import wave
from typing import AsyncIterator

from openai import AsyncOpenAI

from config import OPENAI_API_KEY

# ── constants ────────────────────────────────────────────────────────

_CHUNK_SIZE: int = 4_800
"""4 800 bytes = 0.1 s at 24 kHz int16 mono."""

_DEFAULT_VOICE: str = "nova"
"""Default OpenAI TTS voice — used for live WhatsApp calls. Options: alloy, echo, fable, onyx, nova, shimmer, coral, sage."""

_DEFAULT_MODEL: str = "gpt-4o-mini-tts"

_DEFAULT_INSTRUCTIONS: str = (
    "Speak in a warm, professional, and friendly tone. "
    "You are a helpful restaurant reservation assistant. "
    "When speaking Urdu, use Pakistani Urdu pronunciation and accent "
    "(as spoken in Karachi / Lahore / Islamabad) — NOT Indian Hindi. "
    "Pronounce Persian and Arabic loanwords (e.g. شکریہ shukriya, "
    "آپ aap, خوش آمدید khush-aamdeed, مہربانی meherbani) with their "
    "natural Urdu phonetics. Avoid Hindi/Sanskrit pronunciation patterns. "
    "Keep English words clearly English (don't Urdu-ify proper nouns "
    "like 'The Grand Dine', 'Islamabad', or names)."
)


# ── client factory ────────────────────────────────────────────────────

def _get_openai_client() -> AsyncOpenAI:
    if not OPENAI_API_KEY:
        raise EnvironmentError(
            "OPENAI_API_KEY is not set. Get a key at https://platform.openai.com/api-keys"
        )
    return AsyncOpenAI(api_key=OPENAI_API_KEY)


# ── core synthesis ────────────────────────────────────────────────────

async def synthesise_speech(
    text: str,
    *,
    model: str = _DEFAULT_MODEL,
    voice: str = _DEFAULT_VOICE,
    instructions: str = _DEFAULT_INSTRUCTIONS,
    chunk_size: int = _CHUNK_SIZE,
) -> AsyncIterator[bytes]:
    """Synthesise text to raw PCM audio and yield in chunks.

    Parameters
    ----------
    text : str
        Text to speak.
    model : str
        OpenAI TTS model (default ``gpt-4o-mini-tts``).
    voice : str
        Voice name: alloy, echo, fable, onyx, nova, shimmer.
    instructions : str
        Speaking style instructions passed to the model.
    chunk_size : int
        Bytes per yielded chunk.

    Yields
    ------
    bytes
        Raw 24 kHz 16-bit mono PCM chunks.
    """
    client = _get_openai_client()

    response = await client.audio.speech.create(
        model=model,
        voice=voice,
        input=text,
        response_format="pcm",
        extra_body={"instructions": instructions} if instructions else {},
    )

    audio_bytes: bytes = response.content

    for offset in range(0, len(audio_bytes), chunk_size):
        yield audio_bytes[offset : offset + chunk_size]


async def synthesise_to_bytes(
    text: str,
    *,
    model: str = _DEFAULT_MODEL,
    voice: str = _DEFAULT_VOICE,
    instructions: str = _DEFAULT_INSTRUCTIONS,
) -> bytes:
    """Synthesise text and return all PCM bytes at once."""
    chunks: list[bytes] = []
    async for chunk in synthesise_speech(text, model=model, voice=voice, instructions=instructions):
        chunks.append(chunk)
    return b"".join(chunks)


async def synthesise_to_wav_b64(
    text: str,
    *,
    model: str = _DEFAULT_MODEL,
    voice: str = _DEFAULT_VOICE,
    instructions: str = _DEFAULT_INSTRUCTIONS,
    sample_rate: int = 24_000,
) -> str:
    """Synthesise text and return a base64-encoded WAV string for HTTP responses."""
    pcm = await synthesise_to_bytes(text, model=model, voice=voice, instructions=instructions)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)

    return base64.b64encode(buf.getvalue()).decode("ascii")


# ── TTSModel adapter for VoicePipeline ───────────────────────────────

def create_tts_model(
    model: str = _DEFAULT_MODEL,
    voice: str = _DEFAULT_VOICE,
    instructions: str = _DEFAULT_INSTRUCTIONS,
):
    """Return a TTSModel-compatible object for use with VoicePipeline."""
    from agents.voice import TTSModel, TTSModelSettings

    class _OpenAITTSWrapper(TTSModel):
        def __init__(self) -> None:
            self._model = model
            self._voice = voice
            self._instructions = instructions

        @property
        def model_name(self) -> str:
            return self._model

        async def run(self, text: str, settings: TTSModelSettings) -> AsyncIterator[bytes]:
            resolved_voice = settings.voice if settings.voice else self._voice
            async for chunk in synthesise_speech(
                text,
                model=self._model,
                voice=resolved_voice,
                instructions=self._instructions,
            ):
                yield chunk

    return _OpenAITTSWrapper()
