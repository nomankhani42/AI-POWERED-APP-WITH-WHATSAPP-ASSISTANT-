"""
groq_voice_agent.py
====================
A complete voice agent using the OpenAI Agents SDK with:
  - Groq Whisper for STT (Speech-to-Text) — supports 99+ languages, FREE
  - Groq LLaMA for LLM (chat/reasoning) — FREE
  - Custom STTModel implementation that plugs into VoicePipeline

All parameters are documented with docstrings explaining:
  - What the parameter does
  - Accepted values / types
  - Default values
  - When to change it

Requirements:
    pip install openai-agents[voice] groq numpy sounddevice

Environment Variables:
    GROQ_API_KEY — Your Groq API key from console.groq.com (free, no credit card)
"""

import os
import io
import wave
import asyncio
import numpy as np
from typing import AsyncIterator
from dotenv import load_dotenv

load_dotenv()

from openai import AsyncOpenAI
from agents import Agent, set_default_openai_client, set_tracing_disabled
from agents.voice import (
    AudioInput,
    StreamedAudioInput,
    StreamedTranscriptionSession,
    STTModel,
    STTModelSettings,
    TTSModel,
    VoicePipeline,
    VoicePipelineConfig,
    SingleAgentVoiceWorkflow,
    TTSModelSettings,
)

set_tracing_disabled(True)


# ==============================================================================
# SECTION 1: GROQ CLIENT SETUP
# ==============================================================================

def create_groq_client(
    api_key: str | None = None,
    base_url: str = "https://api.groq.com/openai/v1",
    timeout: float = 30.0,
    max_retries: int = 2,
) -> AsyncOpenAI:
    """
    Creates an async OpenAI-compatible client pointing to Groq's API.

    Groq's API is fully OpenAI-compatible, meaning you can use the
    standard OpenAI SDK just by changing the base_url and api_key.

    Parameters
    ----------
    api_key : str | None
        Your Groq API key from https://console.groq.com/keys
        If None, reads from GROQ_API_KEY environment variable.
        Free tier: no credit card required.

    base_url : str
        The base URL for Groq's OpenAI-compatible API.
        Default: "https://api.groq.com/openai/v1"
        Do not change unless using a proxy or custom deployment.

    timeout : float
        Maximum seconds to wait for a response before raising an error.
        Default: 30.0 seconds
        For voice apps, keep this low (10–30s) to avoid long silences.

    max_retries : int
        Number of times to automatically retry on transient errors (e.g. 503).
        Default: 2
        Set to 0 to disable retries (useful for debugging).

    Returns
    -------
    AsyncOpenAI
        An async client instance ready to make API calls.
    """
    return AsyncOpenAI(
        api_key=api_key or os.environ.get("GROQ_API_KEY"),
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
    )


# ==============================================================================
# SECTION 2: CUSTOM STT MODEL (Whisper via Groq)
# ==============================================================================

class GroqWhisperSTT(STTModel):
    """
    Custom Speech-to-Text model that uses Groq's Whisper API.

    Implements the STTModel interface required by the OpenAI Agents SDK's
    VoicePipeline. Groq hosts Whisper Large V3 on their LPU hardware,
    giving 164–216x real-time transcription speed.

    Supported Models on Groq
    ------------------------
    - whisper-large-v3        : Most accurate, 99+ languages, 8.4% WER
    - whisper-large-v3-turbo  : Faster (216x RT), slightly less accurate
    - distil-whisper-large-v3-en : English only, fastest & cheapest

    Free Tier Limits (resets daily)
    --------------------------------
    - 20 requests/minute
    - 7,200 audio seconds/hour
    - 28,800 audio seconds/day (~8 hours of audio)
    - Max file size: 25 MB
    """

    def __init__(
        self,
        model: str = "whisper-large-v3-turbo",
        language: str | None = None,
        prompt: str | None = None,
        temperature: float = 0.0,
        response_format: str = "json",
        api_key: str | None = None,
    ):
        """
        Initialize the Groq Whisper STT model.

        Parameters
        ----------
        model : str
            Which Whisper model to use on Groq.
            Options:
              - "whisper-large-v3"          → Best accuracy, 99+ languages
              - "whisper-large-v3-turbo"    → Faster, still multilingual (default)
              - "distil-whisper-large-v3-en"→ English only, fastest
            Default: "whisper-large-v3-turbo"

        language : str | None
            ISO-639-1 language code of the audio (e.g. "en", "hi", "fr", "ar").
            If None, Whisper auto-detects the language.
            Providing this improves accuracy and reduces latency.
            Examples: "en" (English), "hi" (Hindi), "es" (Spanish),
                      "fr" (French), "zh" (Chinese), "ar" (Arabic),
                      "de" (German), "ja" (Japanese), "ko" (Korean)
            Default: None (auto-detect)

        prompt : str | None
            Optional context hint for the model (max 224 tokens).
            Use to improve recognition of:
              - Domain-specific words: "This is about API, SDK, Python"
              - Names: "The speaker is named Rahul Sharma"
              - Style: "Formal business conversation"
            Must be in the same language as the audio.
            Default: None

        temperature : float
            Controls randomness in transcription (0.0 to 1.0).
            - 0.0 → Fully deterministic, most consistent (recommended)
            - 0.5 → Some variation
            - 1.0 → Maximum randomness
            For voice agents, always use 0.0 for consistency.
            Default: 0.0

        response_format : str
            Format of the transcription response.
            Options:
              - "json"         → {"text": "..."} (default, fastest)
              - "text"         → Plain string
              - "verbose_json" → Includes timestamps, confidence scores,
                                 language detection, segments
            For the Agents SDK, "json" is sufficient.
            Default: "json"

        api_key : str | None
            Groq API key. If None, reads GROQ_API_KEY env variable.
            Default: None
        """
        self.model = model
        self.language = language
        self.prompt = prompt
        self.temperature = temperature
        self.response_format = response_format
        self.client = create_groq_client(api_key=api_key)

    @property
    def model_name(self) -> str:
        """The name of the STT model."""
        return self.model

    def _numpy_to_wav_bytes(
        self,
        audio_buffer: np.ndarray,
        sample_rate: int = 24000,
        channels: int = 1,
        sample_width: int = 2,
    ) -> io.BytesIO:
        """
        Converts a numpy audio array into WAV format bytes for the API.

        Parameters
        ----------
        audio_buffer : np.ndarray
            Raw PCM audio data as int16 numpy array.
            This is what sounddevice and AudioInput provide.

        sample_rate : int
            Number of audio samples per second (Hz).
            - 8000  → Telephone quality (low)
            - 16000 → Whisper's native rate (optimal for STT)
            - 24000 → OpenAI Agents SDK default
            - 44100 → CD quality (unnecessarily high for STT)
            Groq downsamples to 16kHz internally anyway.
            Default: 24000

        channels : int
            Number of audio channels.
            - 1 → Mono (recommended for STT, smaller file)
            - 2 → Stereo (unnecessary for speech recognition)
            Default: 1

        sample_width : int
            Bytes per audio sample.
            - 1 → 8-bit audio
            - 2 → 16-bit audio (standard, default)
            - 4 → 32-bit audio
            Default: 2

        Returns
        -------
        io.BytesIO
            In-memory WAV file ready to send to the API.
        """
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_buffer.tobytes())
        buf.seek(0)
        buf.name = "audio.wav"  # Required: API needs a filename with extension
        return buf

    async def transcribe(
        self,
        input: AudioInput,
        settings: STTModelSettings,
        trace_include_sensitive_data: bool,
        trace_include_sensitive_audio_data: bool,
    ) -> str:
        """
        Transcribes a complete audio buffer to text.

        Called by VoicePipeline when using AudioInput (non-streaming).
        Best for: push-to-talk, pre-recorded audio, file transcription.

        Parameters
        ----------
        input : AudioInput
            Contains:
              - input.buffer : np.ndarray  → Raw int16 PCM audio samples
              - input.frame_rate : int     → Sample rate (usually 24000)
            Created via: AudioInput(buffer=np.array(...))

        settings : STTModelSettings
            Runtime settings passed by VoicePipeline. Contains:
              - settings.language : str | None  → Override language
              - settings.prompt   : str | None  → Override prompt
              - settings.temperature : float | None → Override temperature
            These override the instance defaults if provided.

        trace_include_sensitive_data : bool
            Whether to include the transcribed text in tracing logs.
            Set False in production to avoid logging user speech.

        trace_include_sensitive_audio_data : bool
            Whether to include the raw audio in tracing logs.
            Always set False in production (audio data is large & private).

        Returns
        -------
        str
            The transcribed text from the audio.
            Returns empty string if no speech detected.
        """
        # Use settings overrides if provided, else fall back to instance defaults
        language = settings.language if settings.language else self.language
        prompt = settings.prompt if settings.prompt else self.prompt
        temperature = (
            settings.temperature
            if settings.temperature is not None
            else self.temperature
        )

        # Convert numpy array to WAV bytes
        frame_rate = getattr(input, "frame_rate", 24000)
        wav_bytes = self._numpy_to_wav_bytes(input.buffer, sample_rate=frame_rate)

        # Call Groq's Whisper API
        result = await self.client.audio.transcriptions.create(
            model=self.model,
            file=wav_bytes,
            language=language,         # ISO-639-1 code or None for auto-detect
            prompt=prompt,             # Context hint (max 224 tokens)
            temperature=temperature,   # 0.0 = deterministic
            response_format=self.response_format,
        )

        return result.text

    async def create_session(
        self,
        input: StreamedAudioInput,
        settings: STTModelSettings,
        trace_include_sensitive_data: bool,
        trace_include_sensitive_audio_data: bool,
    ) -> StreamedTranscriptionSession:
        """
        Creates a streaming transcription session.

        Called by VoicePipeline when using StreamedAudioInput.
        Groq's Whisper API does not support real-time streaming natively,
        so this raises NotImplementedError.

        For streaming, use AudioInput with push-to-talk instead, or
        switch to OpenAI's Realtime API for true streaming STT.

        Parameters
        ----------
        input : StreamedAudioInput
            A stream of audio chunks pushed incrementally.

        Raises
        ------
        NotImplementedError
            Always raised — Groq Whisper does not support streaming sessions.
        """
        raise NotImplementedError(
            "Groq Whisper does not support streaming transcription sessions. "
            "Use AudioInput (push-to-talk) instead of StreamedAudioInput, "
            "or switch to OpenAI Realtime API for streaming STT."
        )


# ==============================================================================
# SECTION 2B: GROQ TTS MODEL (compatible with Groq's API — no `instructions` field)
# ==============================================================================

class GroqTTSModel(TTSModel):
    """
    TTS model that calls Groq's audio speech endpoint without the
    unsupported `instructions` field that OpenAI's default TTS sends.
    """

    DEFAULT_VOICE = "Fritz-PlayAI"

    def __init__(self, model: str = "playai-tts", client: AsyncOpenAI | None = None):
        self.model = model
        self._client = client or create_groq_client()

    @property
    def model_name(self) -> str:
        return self.model

    async def run(self, text: str, settings: TTSModelSettings) -> AsyncIterator[bytes]:
        response = self._client.audio.speech.with_streaming_response.create(
            model=self.model,
            voice=settings.voice or self.DEFAULT_VOICE,
            input=text,
            response_format="pcm",
        )
        async with response as stream:
            async for chunk in stream.iter_bytes(chunk_size=1024):
                yield chunk


# ==============================================================================
# SECTION 3: AGENT SETUP
# ==============================================================================

def create_agent(
    name: str = "Voice Assistant",
    instructions: str = "You are a helpful multilingual voice assistant. Keep responses concise and conversational since they will be spoken aloud.",
    model: str = "llama-3.3-70b-versatile",
    groq_client: AsyncOpenAI | None = None,
) -> Agent:
    """
    Creates an AI agent powered by Groq's LLaMA model.

    Parameters
    ----------
    name : str
        Display name for the agent. Used in tracing and logs.
        Default: "Voice Assistant"

    instructions : str
        System prompt that defines the agent's behavior, personality,
        and constraints. For voice agents, keep responses short since
        they will be converted to speech.
        Tips:
          - "Keep answers under 3 sentences"
          - "Speak naturally, avoid bullet points"
          - "You only help with [specific domain]"
        Default: General multilingual voice assistant

    model : str
        Groq LLM model to use. Free tier options:
          - "llama-3.3-70b-versatile" → Best quality, 6K TPM, 500K TPD
          - "llama-3.1-8b-instant"    → Faster, higher limits (30K TPM)
          - "mixtral-8x7b-32768"      → Good for long contexts (32K window)
          - "gemma2-9b-it"            → Google's model, good multilingual
        Default: "llama-3.3-70b-versatile"

    groq_client : AsyncOpenAI | None
        The Groq async client. If provided, sets it as the global default
        so the agent uses Groq instead of OpenAI.
        Default: None (uses whatever client is already set globally)

    Returns
    -------
    Agent
        Configured agent ready to use in a VoicePipeline.
    """
    if groq_client:
        set_default_openai_client(groq_client)

    return Agent(
        name=name,
        instructions=instructions,
        model=model,
    )


# ==============================================================================
# SECTION 4: VOICE PIPELINE CONFIGURATION
# ==============================================================================

def create_pipeline_config(
    tts_voice: str = "Fritz-PlayAI",
    tts_speed: float = 1.0,
    vad_threshold: float = 0.5,
    silence_duration_ms: int = 700,
) -> VoicePipelineConfig:
    """
    Configures the VoicePipeline settings for TTS output and activity detection.

    Parameters
    ----------
    tts_voice : str
        Voice to use for Text-to-Speech output.
        Groq English voices (PlayAI/Orpheus):
          - "Fritz-PlayAI"    → Male, neutral American English
          - "Celeste-PlayAI"  → Female, clear American English
          - "Arista-PlayAI"   → Female, expressive
          - "Atlas-PlayAI"    → Male, deep
          - "Basil-PlayAI"    → Male, British accent
          - "Briggs-PlayAI"   → Male, authoritative
        Groq Arabic voices:
          - "Amira-PlayAI"    → Female Arabic (Saudi)
          - "Ahmad-PlayAI"    → Male Arabic (Saudi)
        Note: TTS is PAID ($50/M chars). Not on free tier.
        Default: "Fritz-PlayAI"

    tts_speed : float
        Speech rate multiplier for TTS output.
        - 0.5 → Half speed (slow, clear)
        - 1.0 → Normal speed (default)
        - 1.5 → 50% faster
        - 2.0 → Double speed (fast, harder to follow)
        Range: 0.5 to 2.0
        Default: 1.0

    vad_threshold : float
        Voice Activity Detection sensitivity (0.0 to 1.0).
        Used with StreamedAudioInput to detect when speech ends.
        - 0.3 → More sensitive (triggers on quiet speech, more false positives)
        - 0.5 → Balanced (default)
        - 0.8 → Less sensitive (needs louder speech, fewer false triggers)
        Default: 0.5

    silence_duration_ms : int
        Milliseconds of silence after speech before considering utterance complete.
        Used with StreamedAudioInput.
        - 300  → Very responsive, may cut off slow speakers
        - 700  → Good balance (default)
        - 1500 → Patient, good for non-native speakers
        Default: 700

    Returns
    -------
    VoicePipelineConfig
        Configuration object to pass to VoicePipeline.
    """
    tts_settings = TTSModelSettings(
        voice=tts_voice,
        speed=tts_speed,
    )

    return VoicePipelineConfig(
        tts_settings=tts_settings,
    )


# ==============================================================================
# SECTION 5: AUDIO RECORDING UTILITY
# ==============================================================================

def record_audio(
    duration_seconds: float = 5.0,
    sample_rate: int = 24000,
    channels: int = 1,
    dtype: str = "int16",
) -> np.ndarray:
    """
    Records audio from the microphone for a fixed duration.

    Uses sounddevice to capture raw PCM audio from the default
    system microphone. Best for push-to-talk style interactions.

    Parameters
    ----------
    duration_seconds : float
        How many seconds to record.
        - 3.0  → Short commands ("Turn on lights")
        - 5.0  → Medium queries ("What's the weather in Mumbai?")
        - 10.0 → Long questions or dictation
        Default: 5.0

    sample_rate : int
        Audio sample rate in Hz. Must match VoicePipeline expectations.
        - 16000 → Whisper's native rate (optimal quality for STT)
        - 24000 → OpenAI Agents SDK default (use this for compatibility)
        - 44100 → CD quality (wasteful for voice, larger files)
        Default: 24000

    channels : int
        Number of audio channels to record.
        - 1 → Mono (recommended, smaller, sufficient for speech)
        - 2 → Stereo (not needed for STT)
        Default: 1

    dtype : str
        NumPy data type for audio samples.
        - "int16"   → 16-bit PCM, standard for speech (default)
        - "float32" → 32-bit float, higher precision
        - "int32"   → 32-bit integer
        Whisper and Groq expect int16 or float32.
        Default: "int16"

    Returns
    -------
    np.ndarray
        Recorded audio as a numpy array of shape (samples, channels)
        or (samples,) for mono. dtype matches the dtype parameter.
    """
    import sounddevice as sd

    print(f"🎙️  Recording for {duration_seconds}s... Speak now!")
    recording = sd.rec(
        frames=int(duration_seconds * sample_rate),
        samplerate=sample_rate,
        channels=channels,
        dtype=dtype,
    )
    sd.wait()  # Block until recording finishes
    print("✅ Recording complete.")
    return recording


# ==============================================================================
# SECTION 6: MAIN VOICE AGENT RUNNER
# ==============================================================================

async def run_voice_agent(
    audio_buffer: np.ndarray,
    agent: Agent,
    stt_model: GroqWhisperSTT,
    pipeline_config: VoicePipelineConfig | None = None,
) -> str:
    """
    Runs the complete voice pipeline: audio → STT → LLM → TTS → audio output.

    Parameters
    ----------
    audio_buffer : np.ndarray
        Raw int16 PCM audio from the microphone.
        Shape: (num_samples,) or (num_samples, 1) for mono.
        Typically from record_audio() or sounddevice directly.

    agent : Agent
        The configured LLM agent (from create_agent()).
        Handles the conversation logic, tools, and response generation.

    stt_model : GroqWhisperSTT
        The custom Groq Whisper STT model (from GroqWhisperSTT()).
        Handles converting audio to text.

    pipeline_config : VoicePipelineConfig | None
        Optional pipeline configuration (TTS voice, VAD settings, etc).
        If None, uses default VoicePipeline settings.
        Note: TTS output requires a paid Groq tier.
        Default: None

    Returns
    -------
    str
        The agent's text response (before TTS conversion).
        Useful for logging or displaying the response as text.
    """
    # Wrap numpy array in AudioInput for the SDK
    audio_input = AudioInput(buffer=audio_buffer)

    # Build the pipeline with Groq-compatible TTS (no `instructions` field)
    groq_tts = GroqTTSModel(client=create_groq_client())
    pipeline = VoicePipeline(
        workflow=SingleAgentVoiceWorkflow(agent),
        stt_model=stt_model,           # Our custom Groq Whisper STT
        tts_model=groq_tts,            # Our custom Groq TTS (no `instructions`)
        config=pipeline_config,        # TTS and VAD settings
    )

    # Run the pipeline and collect results
    result = await pipeline.run(audio_input)

    # Create an audio player using sounddevice
    import sounddevice as sd
    player = sd.OutputStream(samplerate=24000, channels=1, dtype=np.int16)
    player.start()

    # Stream audio events and play them back
    async for event in result.stream():
        if event.type == "voice_stream_event_audio":
            player.write(event.data)

    player.stop()
    player.close()

    # Return the accumulated transcript text
    return result.total_output_text


# ==============================================================================
# SECTION 7: TRANSCRIBE-ONLY UTILITY (no agent, just STT)
# ==============================================================================

async def transcribe_audio_file(
    file_path: str,
    model: str = "whisper-large-v3",
    language: str | None = None,
    prompt: str | None = None,
    temperature: float = 0.0,
    response_format: str = "verbose_json",
    api_key: str | None = None,
) -> dict:
    """
    Standalone function to transcribe an audio file using Groq Whisper.
    Does NOT use the Agents SDK — calls Groq directly.
    Useful for batch transcription, file processing, or testing.

    Parameters
    ----------
    file_path : str
        Path to the audio file on disk.
        Supported formats: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm
        Max size: 25 MB (free tier), 100 MB (paid tier)
        Example: "/home/user/audio/meeting.mp3"

    model : str
        Whisper model to use. Options:
          - "whisper-large-v3"           → Best accuracy, all languages
          - "whisper-large-v3-turbo"     → Faster, still multilingual
          - "distil-whisper-large-v3-en" → English only, fastest
        Default: "whisper-large-v3" (most accurate for files)

    language : str | None
        ISO-639-1 language code.
        Providing this improves speed and accuracy.
        Common codes:
          "en" English  | "hi" Hindi    | "es" Spanish
          "fr" French   | "de" German   | "zh" Chinese
          "ar" Arabic   | "ja" Japanese | "ko" Korean
          "pt" Portuguese | "ru" Russian | "it" Italian
        None → auto-detect (slightly slower)
        Default: None

    prompt : str | None
        Context hint to improve recognition quality (max 224 tokens).
        Examples:
          - "This is a medical consultation"
          - "Speaker names: Rahul, Priya, Amit"
          - "Technical terms: API, SDK, microservices, Docker"
        Must be in the same language as the audio.
        Default: None

    temperature : float
        Transcription randomness (0.0 to 1.0).
        0.0 → Deterministic, consistent (use for production)
        Higher values → More creative but less reliable
        Default: 0.0

    response_format : str
        Output format of the transcription:
          - "json"         → {"text": "Hello world"}
          - "text"         → "Hello world"  (plain string)
          - "verbose_json" → Full details including:
                             - text: full transcription
                             - language: detected language code
                             - duration: audio length in seconds
                             - segments: list of timed segments with:
                               - id, start, end, text
                               - avg_logprob (confidence, closer to 0 = better)
                               - no_speech_prob (0 = definitely speech)
                               - compression_ratio (normal: 1.0–2.5)
        Default: "verbose_json" (most information)

    api_key : str | None
        Groq API key. None → reads GROQ_API_KEY env variable.
        Default: None

    Returns
    -------
    dict
        For "json": {"text": "transcribed text"}
        For "verbose_json": {
            "text": "full transcription",
            "language": "en",
            "duration": 12.5,
            "segments": [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 3.2,
                    "text": "Hello, how are you?",
                    "avg_logprob": -0.09,      # confidence (0 = perfect)
                    "no_speech_prob": 0.01,    # 0 = definitely speech
                    "compression_ratio": 1.5   # normal speech ratio
                },
                ...
            ]
        }
        For "text": {"text": "plain string"}

    Raises
    ------
    FileNotFoundError
        If file_path does not exist.
    groq.RateLimitError
        If free tier limits exceeded (429). Wait and retry.
    groq.BadRequestError
        If file format unsupported or file too large.
    """
    client = create_groq_client(api_key=api_key)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    with open(file_path, "rb") as audio_file:
        result = await client.audio.transcriptions.create(
            model=model,
            file=audio_file,
            language=language,
            prompt=prompt,
            temperature=temperature,
            response_format=response_format,
        )

    # Normalize output to dict
    if response_format == "text":
        return {"text": result}
    elif hasattr(result, "model_dump"):
        return result.model_dump()
    else:
        return {"text": result.text}


# ==============================================================================
# SECTION 8: TRANSLATION UTILITY (any language → English text)
# ==============================================================================

async def translate_audio_to_english(
    file_path: str,
    model: str = "whisper-large-v3",
    prompt: str | None = None,
    temperature: float = 0.0,
    api_key: str | None = None,
) -> str:
    """
    Translates speech in any language to English text.

    Uses Groq's translation endpoint (different from transcription).
    Transcription → keeps original language.
    Translation   → always outputs English, regardless of input language.

    Parameters
    ----------
    file_path : str
        Path to audio file. Any language supported by Whisper.
        Formats: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm

    model : str
        Must be "whisper-large-v3" for translation.
        (whisper-large-v3-turbo also supports translation)
        Default: "whisper-large-v3"

    prompt : str | None
        Optional context hint for style/spelling guidance.
        Should be in ENGLISH for translation endpoint.
        Default: None

    temperature : float
        Randomness (0.0 = deterministic, recommended).
        Default: 0.0

    api_key : str | None
        Groq API key. None → reads GROQ_API_KEY env variable.
        Default: None

    Returns
    -------
    str
        English translation of the spoken audio.
        Example: Hindi audio "नमस्ते, आप कैसे हैं?" → "Hello, how are you?"
    """
    client = create_groq_client(api_key=api_key)

    with open(file_path, "rb") as audio_file:
        result = await client.audio.translations.create(
            model=model,
            file=audio_file,
            prompt=prompt,
            response_format="json",
            temperature=temperature,
        )

    return result.text


# ==============================================================================
# SECTION 9: MAIN ENTRY POINT — EXAMPLE USAGE
# ==============================================================================

async def main():
    """
    Example: Complete voice agent interaction.

    Flow:
      1. Record 5 seconds of audio from microphone
      2. Transcribe with Groq Whisper (free, 99+ languages)
      3. Send text to LLaMA agent (free)
      4. Get text response back
      (TTS output skipped — requires paid tier)
    """

    # --- Step 1: Set up Groq STT ---
    stt = GroqWhisperSTT(
        model="whisper-large-v3-turbo",  # Fast + multilingual
        language=None,                    # Auto-detect language
        prompt=None,                      # No context hint
        temperature=0.0,                  # Deterministic
        response_format="json",
    )

    # --- Step 2: Set up Groq LLM Agent ---
    groq_client = create_groq_client()
    agent = create_agent(
        name="Multilingual Assistant",
        instructions=(
            "You are a helpful voice assistant. "
            "Keep responses concise (1-3 sentences) since they will be spoken aloud. "
            "Respond in the same language the user speaks."
        ),
        model="llama-3.3-70b-versatile",
        groq_client=groq_client,
    )

    # --- Step 3: Record audio ---
    audio = record_audio(
        duration_seconds=5.0,
        sample_rate=24000,
        channels=1,
        dtype="int16",
    )

    # --- Step 4: Run voice pipeline ---
    response = await run_voice_agent(
        audio_buffer=audio,
        agent=agent,
        stt_model=stt,
    )

    print(f"\n📝 Final response: {response}")


# --- Quick transcription example (no agent) ---
async def transcribe_example():
    """
    Example: Transcribe an audio file and print detailed results.
    """
    result = await transcribe_audio_file(
        file_path="your_audio.mp3",
        model="whisper-large-v3",
        language=None,              # auto-detect
        prompt=None,
        temperature=0.0,
        response_format="verbose_json",
    )

    print(f"📝 Text: {result['text']}")
    print(f"🌍 Language: {result.get('language', 'unknown')}")
    print(f"⏱️  Duration: {result.get('duration', '?')}s")
    print(f"\n📊 Segments:")
    for seg in result.get("segments", []):
        print(
            f"  [{seg['start']:.1f}s → {seg['end']:.1f}s] "
            f"{seg['text'].strip()} "
            f"(confidence: {seg.get('avg_logprob', '?'):.3f})"
        )


if __name__ == "__main__":
    asyncio.run(main())