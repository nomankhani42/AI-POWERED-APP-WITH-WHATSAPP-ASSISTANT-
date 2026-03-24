"""
part2_stt.py — Groq Whisper Speech-to-Text (Function-Modular)
==============================================================

All business logic lives in **standalone functions**.  The thin
``_GroqWhisperSTTWrapper`` subclass exists only because the OpenAI
Agents SDK's ``VoicePipeline`` requires an object that implements the
``STTModel`` abstract interface — it delegates every call to the public
functions below.

Public API
----------
* ``transcribe_audio``   — core async transcription function
* ``create_stt_model``   — factory that returns a ready-to-use STTModel
* ``compute_rms``        — RMS energy of an int16 buffer
* ``numpy_to_wav_bytes`` — numpy → in-memory WAV file

Free-tier limits (daily reset)
------------------------------
    20 req / min · 7 200 audio-sec / hour · 28 800 audio-sec / day

Environment variable:
    GROQ_API_KEY  — from https://console.groq.com/keys

Usage::

    from part2_stt import create_stt_model, transcribe_audio
    stt = create_stt_model()                    # for VoicePipeline
    text = await transcribe_audio(audio_input)   # standalone call
"""

from __future__ import annotations

import asyncio
import io
import math
import wave
from uuid import uuid4

import numpy as np

from agents.voice import (
    AudioInput,
    STTModel,
    STTModelSettings,
    StreamedAudioInput,
    StreamedTranscriptionSession,
)

from part1_groq_client import create_groq_client


# ── constants ────────────────────────────────────────────────────────

_MAX_FILE_BYTES: int = 25 * 1024 * 1024
"""25 MB — Groq hard limit on upload size."""

_SILENCE_RMS_THRESHOLD: float = 50.0
"""RMS amplitude below which audio is considered silent."""

_RETRY_WAIT_SECS: float = 2.0
"""Seconds to wait before retrying after a 429 rate-limit response."""

_MAX_RETRIES: int = 3
"""Maximum retry attempts on 429 errors."""


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
    language: str | None = None,
    prompt: str | None = None,
    temperature: float = 0.0,
    response_format: str = "json",
    silence_threshold: float = _SILENCE_RMS_THRESHOLD,
    client=None,
    api_key: str | None = None,
) -> str:
    """Transcribe an audio buffer to text via Groq Whisper.

    This is the **standalone** transcription function containing all
    business logic (silence gate, settings merge, WAV conversion, API
    call with retry).

    Parameters
    ----------
    audio_input : AudioInput
        Wrapper around the raw audio (``audio_input.buffer``).
    settings : STTModelSettings | None
        Runtime overrides — non-None fields take priority.
    model : str
        Groq Whisper model identifier.
    language : str | None
        ISO 639-1 code or ``None`` for auto-detect.
    prompt : str | None
        Context hint for the model (max 224 tokens).
    temperature : float
        Decoding randomness (0.0 = deterministic).
    response_format : str
        ``"json"`` | ``"text"`` | ``"verbose_json"``.
    silence_threshold : float
        RMS below this → return ``""`` without calling the API.
    client : AsyncOpenAI | None
        Pre-built Groq client.  ``None`` → new one via factory.
    api_key : str | None
        Groq API key (used only when ``client`` is ``None``).

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
    resolved_temperature = (
        settings.temperature
        if settings.temperature is not None
        else temperature
    )

    # ── numpy → WAV bytes ────────────────────────────────────────
    frame_rate = getattr(audio_input, "frame_rate", 24_000)
    wav_bytes = numpy_to_wav_bytes(audio_input.buffer, sample_rate=frame_rate)

    # ── ensure client ────────────────────────────────────────────
    groq_client = client or create_groq_client(api_key=api_key)

    # ── API call with retry on 429 ───────────────────────────────
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result = await groq_client.audio.transcriptions.create(
                model=model,
                file=wav_bytes,
                language=resolved_language,
                prompt=resolved_prompt,
                temperature=resolved_temperature,
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
    language: str | None = None,
    prompt: str | None = None,
    temperature: float = 0.0,
    response_format: str = "json",
    silence_threshold: float = _SILENCE_RMS_THRESHOLD,
    api_key: str | None = None,
) -> STTModel:
    """Create and return an ``STTModel`` for use with ``VoicePipeline``.

    This is the **recommended** way to obtain an STT instance.
    All parameters are forwarded to ``transcribe_audio`` on each call.

    Returns
    -------
    STTModel
        Thin wrapper compatible with VoicePipeline.
    """
    return _GroqWhisperSTTWrapper(
        model=model,
        language=language,
        prompt=prompt,
        temperature=temperature,
        response_format=response_format,
        silence_threshold=silence_threshold,
        api_key=api_key,
    )


# ── thin SDK wrapper (private) ───────────────────────────────────────
# VoicePipeline requires an object implementing STTModel.
# This wrapper delegates ALL logic to the standalone functions above.

class _GroqWhisperSTTWrapper(STTModel):
    """Minimal STTModel adapter — delegates to ``transcribe_audio``."""

    def __init__(
        self,
        model: str = "whisper-large-v3-turbo",
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
        response_format: str = "json",
        silence_threshold: float = _SILENCE_RMS_THRESHOLD,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._language = language
        self._prompt = prompt
        self._temperature = temperature
        self._response_format = response_format
        self._silence_threshold = silence_threshold
        self._client = create_groq_client(api_key=api_key)

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
            temperature=self._temperature,
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
            "Groq Whisper does not support streaming transcription.  "
            "Use AudioInput (push-to-talk) instead of StreamedAudioInput."
        )


# ── backward compatibility alias ─────────────────────────────────────
GroqWhisperSTT = _GroqWhisperSTTWrapper


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
