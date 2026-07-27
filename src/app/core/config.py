"""Application configuration.

A single typed settings object loaded from the environment / ``.env`` (constitution
Principle V). App-level fields carry sensible defaults for this initialization feature;
required secrets added by later features (Meta, Deepgram, Cartesia, OpenAI, Redis,
MongoDB) MUST be declared without a default so startup fails fast when they are missing.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, sourced from environment variables / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "voice-agent"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    # Log level for the app's own loggers (``app.*``). INFO surfaces the call flow — attended,
    # welcome, each transcript, replies, tool calls, fillers, playback, end — in the backend
    # logs. Set to DEBUG to also see interim (partial) transcripts as the caller speaks.
    log_level: str = "INFO"

    # --- Conversational booking assistant (feature 002) ---
    # Required secrets: no default → fail-fast at startup if missing (Principle V).
    openai_api_key: str
    mongodb_uri: str

    # --- Voice call webhook & speech services (feature 003) ---
    # Required secrets: no default → fail-fast at startup if missing (Principle V).
    deepgram_api_key: str
    cartesia_api_key: str
    whatsapp_token: str
    whatsapp_phone_id: str
    whatsapp_verify_token: str
    whatsapp_app_secret: str

    # Defaulted settings.
    agent_model: str = "gpt-4.1"
    mongodb_db: str = "voice_agent"
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 86400  # 1 day
    # Business-local timezone used by the agent when resolving today/tomorrow/relative dates.
    business_timezone: str = "Asia/Karachi"

    # Feature 003 tunables.
    deepgram_model: str = "nova-3"
    deepgram_language: str = "en-US"
    # Nova-3 keyterm prompting: plain comma-separated domain terms (no weights).
    deepgram_keyterms: str = (
        "suite,deluxe,single room,double room,check-in,check-out,room availability,"
        "booking,reservation"
    )
    stt_smart_format: bool = True
    stt_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    deepgram_tts_model: str = "aura-2-thalia-en"
    deepgram_tts_speed: float = Field(default=1.0, ge=0.7, le=1.5)
    cartesia_model: str = "sonic-3.5"
    cartesia_voice_id: str = ""
    # Cartesia managed buffering (ms). When we stream LLM tokens to Cartesia one delta at a
    # time, synthesizing each tiny fragment produces choppy, unclear speech; Cartesia's
    # ``max_buffer_delay_ms`` makes it buffer incoming text until it has enough context (or this
    # delay elapses) before generating, for natural prosody. Cartesia recommends this for
    # token-by-token LLM streaming. 0 disables buffering. Lower it if replies feel laggy.
    cartesia_max_buffer_delay_ms: int = 3000
    stt_sample_rate: int = 16000
    graph_api_version: str = "v21.0"

    # --- Clear playback & real-time streaming loop (feature 004) ---
    # Outbound audio is repacketized into fixed 20 ms frames at this rate to match aiortc's
    # Opus wire format — the fix for the garbled "old TV" playback (research.md §1). The TTS
    # provider synthesizes at the same rate so the media bridge never resamples at the seam.
    tts_output_sample_rate: int = 48000
    # Bound provider stalls so a failed TTS stream cannot leave a caller in silence.
    tts_first_audio_timeout_s: float = Field(default=10.0, gt=0)
    tts_event_timeout_s: float = Field(default=10.0, gt=0)
    # Wait for ICE/DTLS before sending the welcome; pre-connection RTP may be dropped.
    media_connect_timeout_s: float = Field(default=20.0, gt=0)
    # Silence (ms) Deepgram waits before finalizing a caller turn (speech_final); ~0.8 s so a
    # brief mid-sentence pause doesn't cut the caller off (FR-005/FR-012).
    stt_endpointing_ms: int = 800
    # UtteranceEnd backstop (ms of silence) for noisy lines (FR-005).
    stt_utterance_end_ms: int = 1000
    # Extra silent retries of an STT/TTS operation before a spoken apology + continue (FR-011).
    provider_retry_attempts: int = 1
    # Stop playback when the caller starts speaking during a reply (FR-013). Defaults off so
    # assistant answers complete before the no-input timer starts.
    barge_in_enabled: bool = False

    # --- Tool-tailored spoken fillers (feature 005, US3/FR-006/FR-009) ---
    # Spoken the instant a tool call is detected in the agent stream, so the caller is never
    # left in silence while a lookup/booking runs. Tailored per tool; generic is the fallback
    # for any tool without a specific phrase. Configurable so wording changes never touch the
    # loop. filler_for() in services/media/fillers.py maps tool name -> phrase.
    filler_generic: str = "Let me check that for you…"
    filler_check_availability: str = "Let me find that for you…"
    filler_book_room: str = "One moment, I'm booking that…"
    filler_cancel_booking: str = "Let me cancel that…"
    filler_list_bookings: str = "Let me pull up your bookings…"

    # Feature 003 — automatic conversation loop (US4).
    # Greeting spoken automatically when a call is attended (FR-016/FR-022). Configurable
    # so it can change without touching the loop; empty string disables the auto-welcome.
    welcome_message: str = (
        "Hello! Thanks for calling. I can help you check room availability, book a room, "
        "review your bookings, or cancel a booking. How can I help you today?"
    )
    # Seconds of caller silence before the loop re-prompts once, then hangs up gracefully.
    caller_silence_timeout_s: float = 10.0

    # LOCAL DEBUG ONLY: skip X-Hub-Signature-256 verification on the calling webhook.
    # Defaults to secure (False). NEVER enable on a public/production URL — without the
    # check anyone can inject fake call events.
    whatsapp_skip_signature: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance.

    Instantiating ``Settings`` validates every field at first access; a missing required
    field (declared by a future feature) raises immediately, giving fail-fast startup.
    """

    return Settings()
