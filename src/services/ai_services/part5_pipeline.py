"""
part5_pipeline.py — VoicePipeline Wiring & Conversation Loop
==============================================================

Ties together all four previous parts into a working **voice agent**:

    Microphone → Groq Whisper STT → Groq LLaMA Agent → Azure TTS → Speaker

Components used
---------------
* **Part 1** — ``create_groq_client``     (Groq AsyncOpenAI client)
* **Part 2** — ``create_stt_model``       (factory → STTModel)
* **Part 3** — ``voice_agent``            (Agent + tools)
* **Part 4** — ``create_tts_model``       (factory → TTSModel)

Pipeline flow
-------------
1. ``sounddevice`` records 24 kHz int16 mono audio from the default mic.
2. RMS silence check — if the recording is empty we skip the pipeline.
3. ``VoicePipeline.run(AudioInput)`` orchestrates STT → Agent → TTS.
4. ``StreamedAudioResult.stream()`` yields events:
   - ``voice_stream_event_audio``      — PCM bytes → speaker
   - ``voice_stream_event_text``       — agent text delta
   - ``voice_stream_event_transcript`` — STT transcript
5. Conversation loops until the user presses ``Ctrl+C``.

Environment variables required:
    GROQ_API_KEY        — Groq console  (STT + LLM)
    AZURE_SPEECH_KEY    — Azure portal  (TTS)
    AZURE_SPEECH_REGION — e.g. "eastus" (TTS)

Run::

    python part5_pipeline.py
"""

from __future__ import annotations

import asyncio
import sys

import numpy as np

from agents.voice import (
    AudioInput,
    SingleAgentVoiceWorkflow,
    STTModel,
    StreamedAudioResult,
    TTSModel,
    VoicePipeline,
    VoicePipelineConfig,
    TTSModelSettings,
)

# Local parts — function-modular API
from part2_stt import create_stt_model, compute_rms
from part3_agent import voice_agent
from part4_tts import create_tts_model


# ── audio recording ──────────────────────────────────────────────────

def record_from_mic(
    duration_seconds: float = 5.0,
    sample_rate: int = 24_000,
    channels: int = 1,
    dtype: str = "int16",
) -> np.ndarray:
    """Record audio from the system default microphone.

    Uses ``sounddevice`` — a thin Python binding to PortAudio.

    Parameters
    ----------
    duration_seconds : float
        How long to record.

        • **Accepted values:** any positive float.
        • **Default:** 5.0.
        • **When to change:**
          3.0 for short commands ("turn on lights");
          10.0 for longer dictation.

        # 🔴 Learning: fixed duration is simple push-to-talk.
        # ✅ Production: use VAD-based endpoint detection so the user
        #    doesn't have to wait for the full timer.

    sample_rate : int
        Samples per second.

        • **Accepted values:** 8 000 / 16 000 / 24 000 / 44 100 / 48 000.
        • **Default:** 24 000 — matches VoicePipeline expectation.
        • **When to change:** only if the rest of the pipeline changes.

    channels : int
        1 = mono (**recommended**), 2 = stereo.

        • **Default:** 1.

    dtype : str
        NumPy dtype for samples.

        • **Accepted values:** ``"int16"`` (standard), ``"float32"``.
        • **Default:** ``"int16"`` — matches Whisper & the SDK.

    Returns
    -------
    np.ndarray
        Flat 1-D array of shape ``(duration_seconds * sample_rate,)``.
    """
    import sounddevice as sd  # type: ignore[import-untyped]

    print(f"\n🎙️  Recording for {duration_seconds:.1f}s — speak now!")
    audio = sd.rec(
        frames=int(duration_seconds * sample_rate),
        samplerate=sample_rate,
        channels=channels,
        dtype=dtype,
    )
    sd.wait()
    audio = audio.flatten()
    print("✅  Recording complete.")
    return audio


# ── silence gate ─────────────────────────────────────────────────────

_SILENCE_RMS_THRESHOLD: float = 50.0
"""RMS below this value → skip the pipeline (no speech detected).

• **Accepted values:** 0.0 – 32 768.0.
• **Default:** 50.0.
• **When to change:** raise in noisy rooms; lower in quiet studios.

# 🔴 Learning: simple fixed threshold.
# ✅ Production: use WebRTC VAD or silero-vad for robust detection.
"""


def is_silent(audio: np.ndarray, threshold: float = _SILENCE_RMS_THRESHOLD) -> bool:
    """Return ``True`` if the audio buffer is below the silence threshold.

    Parameters
    ----------
    audio : np.ndarray
        int16 PCM samples.
    threshold : float
        RMS cutoff.  See ``_SILENCE_RMS_THRESHOLD``.

    Returns
    -------
    bool
    """
    rms = compute_rms(audio)
    if rms < threshold:
        print(f"🔇  Silence detected (RMS={rms:.1f} < {threshold}).  Skipping.")
        return True
    print(f"🔊  Audio detected (RMS={rms:.1f}).")
    return False


# ── pipeline factory ─────────────────────────────────────────────────

def build_pipeline(
    stt: STTModel | None = None,
    tts: TTSModel | None = None,
    tts_voice: str = "en-US-AvaMultilingualNeural",
    tts_speed: float = 1.0,
) -> VoicePipeline:
    """Construct the full ``VoicePipeline``.

    Parameters
    ----------
    stt : STTModel | None
        Custom STT model.  ``None`` → creates one via ``create_stt_model()``.

    tts : TTSModel | None
        Custom TTS model.  ``None`` → creates one via ``create_tts_model()``.

    tts_voice : str
        Fallback voice name used in ``VoicePipelineConfig``.

    tts_speed : float
        Speech rate multiplier (passed via ``TTSModelSettings``).

    Returns
    -------
    VoicePipeline
        Ready-to-run pipeline instance.
    """
    stt_model = stt or create_stt_model()
    tts_model = tts or create_tts_model()

    config = VoicePipelineConfig(
        tts_settings=TTSModelSettings(
            voice=tts_voice,
            speed=tts_speed,
            # voice : str
            #   Passed to AzureTTS.run() → settings.voice.
            #   Overrides the TTS model's default voice name.
            #
            # speed : float
            #   Standard SDK field (1.0 = normal).
            #   AzureTTS uses SSML <prosody rate=…> instead,
            #   so this is informational only in our setup.
        ),
        # 🔴 Learning: default config is sufficient.
        # ✅ Production: also set stt_settings (language, prompt)
        #    and tracing options here.
    )

    pipeline = VoicePipeline(
        workflow=SingleAgentVoiceWorkflow(voice_agent),
        # workflow : VoiceWorkflow
        #   The agent logic that runs between STT and TTS.
        #   SingleAgentVoiceWorkflow wraps a single Agent.
        #   For multi-agent handoffs, implement a custom VoiceWorkflow.
        stt_model=stt_model,
        # stt_model : STTModel
        #   Our Groq Whisper implementation from part2.
        tts_model=tts_model,
        # tts_model : TTSModel
        #   Our Azure TTS implementation from part4.
        config=config,
        # config : VoicePipelineConfig
        #   TTS settings, STT settings, tracing flags.
    )

    return pipeline


# ── event streamer ───────────────────────────────────────────────────

async def stream_result(result: StreamedAudioResult) -> str:
    """Consume all events from a pipeline run.

    Handles every event type emitted by ``StreamedAudioResult.stream()``:

    * ``voice_stream_event_audio``      — raw PCM bytes → play
    * ``voice_stream_event_text``       — agent text token
    * ``voice_stream_event_transcript`` — STT transcript of user speech

    Parameters
    ----------
    result : StreamedAudioResult
        The return value of ``pipeline.run(audio_input)``.

    Returns
    -------
    str
        The complete agent text response (concatenated from text events).
    """
    import sounddevice as sd  # type: ignore[import-untyped]

    # ── audio player setup ───────────────────────────────────────
    player = sd.OutputStream(
        samplerate=24_000,
        # samplerate : int — must match TTS output (24 kHz).
        channels=1,
        # channels : int — mono.
        dtype=np.int16,
        # dtype : str — matches Raw24Khz16BitMonoPcm.
    )
    player.start()

    full_text = ""

    # ── event loop ───────────────────────────────────────────────
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            # Raw PCM int16 bytes → write to speaker.
            # 🔴 Learning: direct write is fine for local playback.
            # ✅ Production: buffer + jitter compensation for
            #    network-streamed audio.
            player.write(np.frombuffer(event.data, dtype=np.int16))

        elif event.type == "voice_stream_event_text":
            # Agent text token (streamed word-by-word).
            print(event.data, end="", flush=True)
            full_text += event.data

        elif event.type == "voice_stream_event_transcript":
            # STT transcript of user's speech.
            print(f"\n📝  You said: {event.data}")

        elif event.type == "voice_stream_event_lifecycle":
            # Pipeline lifecycle events (start, end, etc.)
            # 🔴 Learning: ignore for simplicity.
            # ✅ Production: log for observability.
            pass

        elif event.type == "voice_stream_event_error":
            # Error during pipeline execution.
            print(f"\n❌  Pipeline error: {event.data}", file=sys.stderr)

    # ── cleanup ──────────────────────────────────────────────────
    player.stop()
    player.close()

    return full_text


# ── conversation loop ────────────────────────────────────────────────

async def conversation_loop(
    duration_seconds: float = 5.0,
    sample_rate: int = 24_000,
) -> None:
    """Run an interactive push-to-talk voice conversation.

    The loop:
    1. Waits for ``Enter`` key → records audio.
    2. Checks for silence → skips if quiet.
    3. Runs the VoicePipeline → STT → Agent → TTS → Speaker.
    4. Prints transcript + agent reply.
    5. Repeat until ``Ctrl+C``.

    Parameters
    ----------
    duration_seconds : float
        Recording length per turn.

        • **Default:** 5.0.
        • **When to change:** shorter (3.0) for command-driven UIs;
          longer (10.0) for dictation / storytelling.

    sample_rate : int
        Audio sample rate.

        • **Default:** 24 000.
        • **When to change:** only if the STT / TTS models change.
    """
    print("=" * 60)
    print("🎤  VOICE AGENT — Push-to-Talk")
    print("=" * 60)
    print("Press Enter to start recording, Ctrl+C to quit.\n")

    # Build the pipeline once (reuse across turns).
    # 🔴 Learning: rebuild per-turn would also work but wastes time.
    # ✅ Production: single pipeline instance + connection pooling.
    pipeline = build_pipeline()

    turn = 0
    while True:
        try:
            input("⏎  Press Enter to speak …")  # noqa: A003
        except KeyboardInterrupt:
            print("\n👋  Goodbye!")
            break

        turn += 1
        print(f"\n── Turn {turn} ──")

        try:
            # ── record ───────────────────────────────────────────
            audio = record_from_mic(
                duration_seconds=duration_seconds,
                sample_rate=sample_rate,
            )

            # ── silence check ────────────────────────────────────
            if is_silent(audio):
                continue

            # ── run pipeline ─────────────────────────────────────
            audio_input = AudioInput(buffer=audio)
            # AudioInput : agents.voice.AudioInput
            #   Wraps a numpy buffer for the pipeline.
            #   • buffer — int16 ndarray
            #   • frame_rate — inferred from pipeline config (24 kHz)

            result: StreamedAudioResult = await pipeline.run(audio_input)
            # result : StreamedAudioResult
            #   Async iterable of voice_stream_event_* events.
            #   Call result.stream() to consume them.

            # ── stream events (play audio + print text) ──────────
            reply = await stream_result(result)
            print(f"\n🤖  Agent: {reply}\n")

        except KeyboardInterrupt:
            print("\n👋  Goodbye!")
            break
        except Exception as exc:
            # ── generic exception handler ────────────────────────
            # 🔴 Learning: print & continue keeps the demo alive.
            # ✅ Production: structured logging, error tracking
            #    (Sentry), graceful degradation (e.g. fallback TTS).
            print(f"\n❌  Error in turn {turn}: {type(exc).__name__}: {exc}")
            print("    Continuing to next turn …\n")
            continue


# ── main entry point ─────────────────────────────────────────────────

async def main() -> None:
    """Entry point — starts the interactive voice conversation loop.

    Flow
    ----
    1. Groq client initialised (via part1 → part2 & part3 imports).
    2. Azure TTS initialised (via part4 import in ``build_pipeline``).
    3. VoicePipeline assembled: STT + Agent + TTS.
    4. Conversation loop: record → transcribe → think → speak → repeat.

    Environment
    -----------
    GROQ_API_KEY        — Groq free-tier key
    AZURE_SPEECH_KEY    — Azure Speech resource key
    AZURE_SPEECH_REGION — Azure region (e.g. ``"eastus"``)
    """
    await conversation_loop(
        duration_seconds=5.0,
        sample_rate=24_000,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋  Goodbye!")
