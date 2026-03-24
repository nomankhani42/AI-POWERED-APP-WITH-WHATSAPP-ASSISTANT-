"""
part4_tts.py — Azure Cognitive Services Text-to-Speech (Function-Modular)
==========================================================================

All business logic lives in **standalone functions**.  The thin
``_AzureTTSWrapper`` subclass exists only because the OpenAI Agents
SDK's ``VoicePipeline`` requires an object implementing ``TTSModel``.

Public API
----------
* ``create_speech_config``        — factory for Azure SpeechConfig
* ``synthesise_speech_blocking``  — synchronous synthesis (runs in thread)
* ``synthesise_speech``           — async generator yielding PCM chunks
* ``create_tts_model``            — factory returning a ready-to-use TTSModel
* ``build_ssml``                  — SSML document builder
* ``sanitise_xml``                — XML character escaping

Environment variables:
    AZURE_SPEECH_KEY    — from Azure Portal → Speech resource → Keys
    AZURE_SPEECH_REGION — e.g. ``"eastus"``, ``"westeurope"``

Usage::

    from part4_tts import create_tts_model, synthesise_speech
    tts = create_tts_model()                  # for VoicePipeline
    async for chunk in synthesise_speech(…):  # standalone call
        play(chunk)
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

import numpy as np

from agents.voice import TTSModel, TTSModelSettings

# ── Azure SDK import ─────────────────────────────────────────────────
try:
    import azure.cognitiveservices.speech as speechsdk
except ImportError as exc:
    raise ImportError(
        "Azure Speech SDK not installed.  Run:\n"
        "  pip install azure-cognitiveservices-speech\n"
        "or:  uv add azure-cognitiveservices-speech"
    ) from exc


# ── constants ────────────────────────────────────────────────────────

_CHUNK_SIZE: int = 4_800
"""Bytes per yielded audio chunk (4 800 = 0.1 s at 24 kHz int16 mono)."""

_THREAD_POOL = ThreadPoolExecutor(max_workers=2)
"""Dedicated thread-pool for blocking Azure SDK calls."""


# ── XML sanitisation ─────────────────────────────────────────────────

def sanitise_xml(text: str) -> str:
    """Escape characters that would break SSML XML parsing.

    Parameters
    ----------
    text : str
        Raw text to embed inside an SSML element.

    Returns
    -------
    str
        Text with ``&``, ``<``, ``>``, ``"`` XML-escaped.
    """
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


# keep old name for backward compat
_sanitise_xml = sanitise_xml


# ── SSML builder ─────────────────────────────────────────────────────

def build_ssml(
    text: str,
    voice_name: str = "en-US-AvaMultilingualNeural",
    language: str = "en-US",
    emotion: str | None = None,
    emotion_degree: float = 1.0,
    rate: str = "medium",
    pitch: str = "medium",
) -> str:
    """Build an SSML document for Azure Speech synthesis.

    Parameters
    ----------
    text : str
        The sentence(s) to synthesise.
    voice_name : str
        Azure neural voice identifier.
    language : str
        BCP-47 language code for ``<speak>`` root.
    emotion : str | None
        ``mstts:express-as`` style (e.g. ``"cheerful"``), or ``None``.
    emotion_degree : float
        Intensity of the emotion (0.01 – 2.0).
    rate : str
        Speaking speed (``"x-slow"`` … ``"x-fast"`` or percentage).
    pitch : str
        Voice pitch (``"x-low"`` … ``"x-high"`` or Hz offset).

    Returns
    -------
    str
        Well-formed SSML string.
    """
    safe_text = sanitise_xml(text)
    inner = f"<prosody rate='{rate}' pitch='{pitch}'>{safe_text}</prosody>"

    if emotion:
        inner = (
            f"<mstts:express-as style='{emotion}' "
            f"styledegree='{emotion_degree}'>"
            f"{inner}"
            f"</mstts:express-as>"
        )

    ssml = (
        f"<speak version='1.0' "
        f"xml:lang='{language}' "
        f"xmlns='http://www.w3.org/2001/10/synthesis' "
        f"xmlns:mstts='http://www.w3.org/2001/mstts'>"
        f"<voice name='{voice_name}'>"
        f"{inner}"
        f"</voice>"
        f"</speak>"
    )
    return ssml


# ── Azure SpeechConfig factory ───────────────────────────────────────

def create_speech_config(
    speech_key: str | None = None,
    speech_region: str | None = None,
) -> "speechsdk.SpeechConfig":
    """Create an Azure SpeechConfig for Raw 24 kHz 16-bit mono PCM.

    Parameters
    ----------
    speech_key : str | None
        Azure Speech resource key.  ``None`` → ``AZURE_SPEECH_KEY`` env.
    speech_region : str | None
        Azure region.  ``None`` → ``AZURE_SPEECH_REGION`` env.

    Returns
    -------
    speechsdk.SpeechConfig
        Configured for Raw24Khz16BitMonoPcm output.

    Raises
    ------
    EnvironmentError
        If key or region is missing.
    """
    key = speech_key or os.environ.get("AZURE_SPEECH_KEY")
    region = speech_region or os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        raise EnvironmentError(
            "AZURE_SPEECH_KEY and AZURE_SPEECH_REGION must be set.  "
            "Get free keys at https://portal.azure.com → Speech resource → Keys."
        )

    config = speechsdk.SpeechConfig(subscription=key, region=region)
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm
    )
    return config


# ── synchronous synthesis (runs in thread pool) ──────────────────────

def synthesise_speech_blocking(
    ssml: str,
    speech_config: "speechsdk.SpeechConfig",
) -> bytes:
    """Synthesise SSML to raw PCM bytes (blocking).

    Designed to be called from a thread pool via ``run_in_executor``.

    Parameters
    ----------
    ssml : str
        Complete SSML document.
    speech_config : speechsdk.SpeechConfig
        Azure speech configuration.

    Returns
    -------
    bytes
        Raw PCM audio (24 kHz, 16-bit, mono).

    Raises
    ------
    RuntimeError
        If Azure returns a cancellation or unexpected result.
    """
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=None,
    )

    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return result.audio_data

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        msg = f"Azure TTS canceled: {details.reason}"
        if details.reason == speechsdk.CancellationReason.Error:
            msg += f" | error: {details.error_details}"
            msg += (
                "\nHint: check AZURE_SPEECH_KEY and AZURE_SPEECH_REGION "
                "env vars, or verify your Azure Speech quota."
            )
        raise RuntimeError(msg)

    raise RuntimeError(
        f"Unexpected Azure TTS result reason: {result.reason}"
    )


# ── async synthesis generator ────────────────────────────────────────

async def synthesise_speech(
    text: str,
    speech_config: "speechsdk.SpeechConfig",
    *,
    voice_name: str = "en-US-AvaMultilingualNeural",
    language: str = "en-US",
    emotion: str | None = None,
    emotion_degree: float = 1.0,
    rate: str = "medium",
    pitch: str = "medium",
    chunk_size: int = _CHUNK_SIZE,
) -> AsyncIterator[bytes]:
    """Synthesise text to PCM audio and yield in chunks.

    This is the **standalone** async generator that can be called
    independently of the VoicePipeline.

    Parameters
    ----------
    text : str
        Plain text to speak (not SSML).
    speech_config : speechsdk.SpeechConfig
        Azure speech configuration.
    voice_name : str
        Azure neural voice.
    language : str
        BCP-47 locale.
    emotion : str | None
        Emotion style or ``None``.
    emotion_degree : float
        Emotion intensity.
    rate : str
        Speaking rate.
    pitch : str
        Voice pitch.
    chunk_size : int
        Bytes per yielded chunk.

    Yields
    ------
    bytes
        Raw 24 kHz 16-bit mono PCM audio chunks.
    """
    ssml = build_ssml(
        text=text,
        voice_name=voice_name,
        language=language,
        emotion=emotion,
        emotion_degree=emotion_degree,
        rate=rate,
        pitch=pitch,
    )

    loop = asyncio.get_running_loop()
    audio_data: bytes = await loop.run_in_executor(
        _THREAD_POOL, synthesise_speech_blocking, ssml, speech_config
    )

    for offset in range(0, len(audio_data), chunk_size):
        yield audio_data[offset : offset + chunk_size]


# ── collect all chunks into numpy ────────────────────────────────────

async def synthesise_to_numpy(
    text: str,
    speech_config: "speechsdk.SpeechConfig",
    voice_name: str = "en-US-AvaMultilingualNeural",
    **kwargs,
) -> np.ndarray:
    """Convenience: collect all TTS chunks into a single numpy array.

    Parameters
    ----------
    text : str
        Text to synthesise.
    speech_config : speechsdk.SpeechConfig
        Azure speech configuration.
    voice_name : str
        Override voice name.
    **kwargs
        Forwarded to ``synthesise_speech``.

    Returns
    -------
    np.ndarray
        Flat int16 array of PCM samples at 24 kHz.
    """
    chunks: list[bytes] = []
    async for chunk in synthesise_speech(
        text, speech_config, voice_name=voice_name, **kwargs
    ):
        chunks.append(chunk)
    raw = b"".join(chunks)
    return np.frombuffer(raw, dtype=np.int16)


# ── factory function ─────────────────────────────────────────────────

def create_tts_model(
    voice_name: str = "en-US-AvaMultilingualNeural",
    language: str = "en-US",
    emotion: str | None = None,
    emotion_degree: float = 1.0,
    rate: str = "medium",
    pitch: str = "medium",
    speech_key: str | None = None,
    speech_region: str | None = None,
) -> TTSModel:
    """Create and return a ``TTSModel`` for use with ``VoicePipeline``.

    This is the **recommended** way to obtain a TTS instance.

    Parameters
    ----------
    voice_name : str
        Azure neural voice.
    language : str
        BCP-47 locale.
    emotion : str | None
        Default emotion style.
    emotion_degree : float
        Emotion intensity.
    rate : str
        Speaking rate.
    pitch : str
        Voice pitch.
    speech_key : str | None
        Azure key override.
    speech_region : str | None
        Azure region override.

    Returns
    -------
    TTSModel
        Thin wrapper compatible with VoicePipeline.
    """
    return _AzureTTSWrapper(
        voice_name=voice_name,
        language=language,
        emotion=emotion,
        emotion_degree=emotion_degree,
        rate=rate,
        pitch=pitch,
        speech_key=speech_key,
        speech_region=speech_region,
    )


# ── thin SDK wrapper (private) ───────────────────────────────────────
# VoicePipeline requires an object implementing TTSModel.
# This wrapper delegates ALL logic to the standalone functions above.

class _AzureTTSWrapper(TTSModel):
    """Minimal TTSModel adapter — delegates to standalone functions."""

    def __init__(
        self,
        voice_name: str = "en-US-AvaMultilingualNeural",
        language: str = "en-US",
        emotion: str | None = None,
        emotion_degree: float = 1.0,
        rate: str = "medium",
        pitch: str = "medium",
        speech_key: str | None = None,
        speech_region: str | None = None,
    ) -> None:
        self.voice_name = voice_name
        self.language = language
        self.emotion = emotion
        self.emotion_degree = emotion_degree
        self.rate = rate
        self.pitch = pitch
        self._speech_config = create_speech_config(speech_key, speech_region)

    @property
    def model_name(self) -> str:
        return f"azure-tts-{self.voice_name}"

    async def run(
        self,
        text: str,
        settings: TTSModelSettings,
    ) -> AsyncIterator[bytes]:
        voice = settings.voice if settings.voice else self.voice_name

        async for chunk in synthesise_speech(
            text=text,
            speech_config=self._speech_config,
            voice_name=voice,
            language=self.language,
            emotion=self.emotion,
            emotion_degree=self.emotion_degree,
            rate=self.rate,
            pitch=self.pitch,
        ):
            yield chunk


# ── backward compatibility alias ─────────────────────────────────────
AzureTTS = _AzureTTSWrapper


# ── self-test ────────────────────────────────────────────────────────

async def _self_test() -> None:
    """Synthesise a test sentence and play through speakers."""
    import sounddevice as sd  # type: ignore[import-untyped]

    speech_cfg = create_speech_config()
    test_text = "Hello! I am your voice assistant powered by Azure. How can I help you today?"
    print(f"🔊  Synthesising: {test_text!r}")

    audio = await synthesise_to_numpy(test_text, speech_cfg)
    print(f"✅  Got {len(audio)} samples ({len(audio) / 24_000:.2f} s)")

    print("▶️  Playing …")
    sd.play(audio, samplerate=24_000)
    sd.wait()
    print("✅  Done.")


if __name__ == "__main__":
    asyncio.run(_self_test())
