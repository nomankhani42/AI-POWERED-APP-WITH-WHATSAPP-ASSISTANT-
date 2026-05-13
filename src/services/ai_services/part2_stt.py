"""
part2_stt.py — Groq Whisper Speech-to-Text
============================================

All business logic lives in **standalone functions**. The thin
``_GroqSTTWrapper`` subclass exists only because the OpenAI Agents
SDK's ``VoicePipeline`` requires an object that implements the
``STTModel`` abstract interface — it delegates every call to the public
functions below.

Why Groq Whisper (whisper-large-v3-turbo)?
------------------------------------------
* ~200 ms latency — fast enough for live voice calls.
* Prompt field works like original Whisper (vocabulary biasing /
  previous-transcript context) — no instruction-echo risk.
* OpenAI-compatible API — same SDK call, only base_url + key differ.
* Supports temperature, verbose_json, and all standard Whisper params.

Public API
----------
* ``transcribe_audio``   — core async transcription function
* ``create_stt_model``   — factory that returns a ready-to-use STTModel
* ``compute_rms``        — RMS energy of an int16 buffer
* ``numpy_to_wav_bytes`` — numpy → in-memory WAV file

Environment variable:
    GROQ_API_KEY  — from https://console.groq.com/keys
"""

from __future__ import annotations

import asyncio
import io
import math
import wave
from uuid import uuid4

import numpy as np
from openai import AsyncOpenAI

from agents.voice import (
    AudioInput,
    STTModel,
    STTModelSettings,
    StreamedAudioInput,
    StreamedTranscriptionSession,
)

from config import GROQ_API_KEY


def _create_groq_client() -> AsyncOpenAI:
    """Async Groq client (OpenAI-compatible) used for Whisper transcription."""
    if not GROQ_API_KEY:
        raise EnvironmentError(
            "GROQ_API_KEY is not set. Get a free key at https://console.groq.com/keys"
        )
    return AsyncOpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
        timeout=15.0,
        max_retries=2,
    )


# ── constants ────────────────────────────────────────────────────────

_MAX_FILE_BYTES: int = 25 * 1024 * 1024
"""25 MB — Groq hard limit on transcription upload size."""

_SILENCE_RMS_THRESHOLD: float = 200.0
"""RMS amplitude below which audio is considered silent (0–32768 scale).
200 catches genuine near-silence while still allowing soft speech through.
"""

_RETRY_WAIT_SECS: float = 2.0
"""Seconds to wait before retrying after a 429 rate-limit response."""

_MAX_RETRIES: int = 3
"""Maximum retry attempts on 429 errors."""

_DEFAULT_PROMPT: str | None = None
"""No default prompt — keep None unless you have a tested vocabulary list.
Groq Whisper treats the prompt as a previous-transcript context hint (not
an instruction), so it is less likely to echo than gpt-4o-mini-transcribe,
but a bad prompt can still bias transcription unexpectedly."""

_DEFAULT_LANGUAGE: str = "en"
"""Locked to English. Switch to None for auto-detect if callers mix
languages and Groq handles the code-switching correctly in testing."""


# ── helper functions ─────────────────────────────────────────────────

def compute_rms(audio: np.ndarray) -> float:
    """Compute the Root-Mean-Square amplitude of an int16 audio buffer.

    Parameters
    ----------
    audio : np.ndarray
        Raw PCM samples, dtype ``int16``.

    Returns
    -------
    float
        RMS value (0.0 = silence, ~23 170 = full-scale).
    """
    samples = audio.flatten().astype(np.float64)
    return float(math.sqrt(np.mean(samples ** 2)))


def numpy_to_wav_bytes(
    audio_buffer: np.ndarray,
    sample_rate: int = 24_000,
    channels: int = 1,
    sample_width: int = 2,
) -> io.BytesIO:
    """Convert a numpy int16 array into an in-memory WAV file.

    Parameters
    ----------
    audio_buffer : np.ndarray
        Raw PCM audio, dtype ``int16``.
    sample_rate : int
        Samples per second (default 24 000).
    channels : int
        1 = mono (default), 2 = stereo.
    sample_width : int
        Bytes per sample (default 2 = 16-bit).

    Returns
    -------
    io.BytesIO
        Seeked-to-zero BytesIO with a unique ``.wav`` filename.

    Raises
    ------
    ValueError
        If the resulting WAV exceeds ``_MAX_FILE_BYTES``.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_buffer.tobytes())

    size = buf.tell()
    if size > _MAX_FILE_BYTES:
        raise ValueError(
            f"WAV size {size / 1024 / 1024:.1f} MB exceeds the "
            f"{_MAX_FILE_BYTES / 1024 / 1024:.0f} MB Groq limit.  "
            "Record shorter audio or down-sample to 16 kHz."
        )

    buf.seek(0)
    buf.name = f"{uuid4().hex}.wav"
    return buf


# ── core transcription function ──────────────────────────────────────

async def transcribe_audio(
    audio_input: AudioInput,
    settings: STTModelSettings | None = None,
    *,
    model: str = "whisper-large-v3-turbo",
    language: str | None = _DEFAULT_LANGUAGE,
    prompt: str | None = _DEFAULT_PROMPT,
    response_format: str = "json",
    silence_threshold: float = _SILENCE_RMS_THRESHOLD,
    client: AsyncOpenAI | None = None,
) -> str:
    """Transcribe an audio buffer to text via Groq Whisper.

    Parameters
    ----------
    audio_input : AudioInput
        Wrapper around the raw audio (``audio_input.buffer``).
    settings : STTModelSettings | None
        Runtime overrides — non-None fields take priority.
    model : str
        Groq Whisper model. ``whisper-large-v3-turbo`` (default) is the
        best balance of speed and accuracy. Use ``whisper-large-v3`` for
        maximum accuracy at slightly higher latency.
    language : str | None
        ISO 639-1 code or ``None`` for auto-detect.
    prompt : str | None
        Previous-transcript context hint (Whisper style) — biases
        vocabulary without echoing instructions into the output.
    response_format : str
        ``"json"``, ``"text"``, or ``"verbose_json"``.
    silence_threshold : float
        RMS below this → return ``""`` without calling the API.
    client : AsyncOpenAI | None
        Pre-built client. ``None`` → Groq client via factory.

    Returns
    -------
    str
        Transcribed text, or ``""`` if the audio is silent.
    """
    settings = settings or STTModelSettings()

    # ── silence gate ─────────────────────────────────────────────
    rms = compute_rms(audio_input.buffer)
    if rms < silence_threshold:
        return ""

    # ── resolve settings (runtime overrides win) ─────────────────
    resolved_language = settings.language if settings.language else language
    resolved_prompt = settings.prompt if settings.prompt else prompt

    # ── numpy → WAV bytes ────────────────────────────────────────
    frame_rate = getattr(audio_input, "frame_rate", 16_000)
    wav_bytes = numpy_to_wav_bytes(audio_input.buffer, sample_rate=frame_rate)

    # ── ensure client ────────────────────────────────────────────
    groq_client = client or _create_groq_client()

    # ── API call with retry on 429 ───────────────────────────────
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result = await groq_client.audio.transcriptions.create(
                model=model,
                file=wav_bytes,
                language=resolved_language,
                prompt=resolved_prompt,
                response_format=response_format,
            )
            return result.text
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(
                exc, "http_status", None
            )
            if status == 429 and attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_WAIT_SECS * attempt)
                wav_bytes.seek(0)
                last_exc = exc
                continue
            raise

    raise last_exc  # type: ignore[misc]


# ── factory function ─────────────────────────────────────────────────

def create_stt_model(
    model: str = "whisper-large-v3-turbo",
    language: str | None = _DEFAULT_LANGUAGE,
    prompt: str | None = _DEFAULT_PROMPT,
    response_format: str = "json",
    silence_threshold: float = _SILENCE_RMS_THRESHOLD,
) -> STTModel:
    """Create and return a Groq Whisper ``STTModel``.

    All parameters are forwarded to ``transcribe_audio`` on each call.
    """
    return _GroqSTTWrapper(
        model=model,
        language=language,
        prompt=prompt,
        response_format=response_format,
        silence_threshold=silence_threshold,
    )


# ── thin SDK wrapper (private) ───────────────────────────────────────
# VoicePipeline requires an object implementing STTModel.
# This wrapper delegates ALL logic to the standalone functions above.

class _GroqSTTWrapper(STTModel):
    """Minimal STTModel adapter for Groq Whisper — delegates to ``transcribe_audio``."""

    def __init__(
        self,
        model: str = "whisper-large-v3-turbo",
        language: str | None = _DEFAULT_LANGUAGE,
        prompt: str | None = _DEFAULT_PROMPT,
        response_format: str = "json",
        silence_threshold: float = _SILENCE_RMS_THRESHOLD,
    ) -> None:
        self._model = model
        self._language = language
        self._prompt = prompt
        self._response_format = response_format
        self._silence_threshold = silence_threshold
        self._client = _create_groq_client()

    @property
    def model_name(self) -> str:
        return self._model

    async def transcribe(
        self,
        input: AudioInput,
        settings: STTModelSettings,
        trace_include_sensitive_data: bool,
        trace_include_sensitive_audio_data: bool,
    ) -> str:
        return await transcribe_audio(
            audio_input=input,
            settings=settings,
            model=self._model,
            language=self._language,
            prompt=self._prompt,
            response_format=self._response_format,
            silence_threshold=self._silence_threshold,
            client=self._client,
        )

    async def create_session(
        self,
        input: StreamedAudioInput,
        settings: STTModelSettings,
        trace_include_sensitive_data: bool,
        trace_include_sensitive_audio_data: bool,
    ) -> StreamedTranscriptionSession:
        raise NotImplementedError(
            "Streaming transcription not wired up here. "
            "Use AudioInput (push-to-talk) instead of StreamedAudioInput."
        )


# ── self-test ────────────────────────────────────────────────────────

async def _self_test() -> None:
    """Record 4 s from the microphone and transcribe via Groq Whisper."""
    import sounddevice as sd  # type: ignore[import-untyped]

    DURATION = 4.0
    RATE = 24_000

    print(f"🎙️  Recording {DURATION}s at {RATE} Hz … speak now!")
    audio = sd.rec(int(DURATION * RATE), samplerate=RATE, channels=1, dtype="int16")
    sd.wait()
    audio = audio.flatten()
    print(f"✅  Captured {len(audio)} samples  (RMS={compute_rms(audio):.1f})")

    text = await transcribe_audio(audio_input=AudioInput(buffer=audio))
    print(f"📝  Transcription: {text!r}")


if __name__ == "__main__":
    asyncio.run(_self_test())
