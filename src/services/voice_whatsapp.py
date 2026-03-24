"""Voice-over-WhatsApp service.

Handles incoming WhatsApp voice messages:

    WhatsApp audio → download → Groq Whisper STT → text
    text → Agent → reply text → send as text message

This module exposes a single public function:
    ``handle_voice_message(sender, media_id)``
"""

from __future__ import annotations

import io
from typing import Any

import httpx
import numpy as np

from config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_API_BASE, WHATSAPP_PHONE_NUMBER_ID

# ── STT (lazy-initialised) ───────────────────────────────────────────

_stt_model = None


def _get_stt():
    """Return (and cache) an STTModel singleton via factory function."""
    global _stt_model
    if _stt_model is None:
        import sys, pathlib
        _ai_dir = str(pathlib.Path(__file__).resolve().parent / "ai_services")
        if _ai_dir not in sys.path:
            sys.path.insert(0, _ai_dir)

        from services.ai_services.part2_stt import create_stt_model
        _stt_model = create_stt_model()
    return _stt_model


# ── WhatsApp media helpers ───────────────────────────────────────────

def _wa_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}


async def download_whatsapp_media(media_id: str) -> bytes:
    """Download a media file from WhatsApp Cloud API.

    Steps (per Meta docs):
    1. GET /{media_id} → returns JSON with a ``url`` field.
    2. GET the ``url`` with the auth header → returns binary content.

    Parameters
    ----------
    media_id : str
        The media ID from the incoming webhook payload
        (``msg["audio"]["id"]``).

    Returns
    -------
    bytes
        Raw audio bytes (usually OGG/Opus from WhatsApp).
    """
    # Step 1: resolve media URL
    async with httpx.AsyncClient(timeout=30) as client:
        meta_resp = await client.get(
            f"{WHATSAPP_API_BASE}/{media_id}",
            headers=_wa_headers(),
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json()["url"]

        # Step 2: download actual file
        dl_resp = await client.get(media_url, headers=_wa_headers())
        dl_resp.raise_for_status()
        return dl_resp.content


# ── audio conversion helpers ─────────────────────────────────────────

def _ogg_bytes_to_numpy(audio_bytes: bytes) -> np.ndarray:
    """Convert OGG/Opus (WhatsApp audio) to int16 numpy array at 24 kHz."""
    try:
        from pydub import AudioSegment

        segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
        segment = segment.set_frame_rate(24_000).set_channels(1).set_sample_width(2)
        raw = segment.raw_data
        return np.frombuffer(raw, dtype=np.int16)
    except ImportError:
        raise ImportError(
            "pydub is required to decode WhatsApp OGG audio.  "
            "Install it:  pip install pydub\n"
            "Also install ffmpeg:  sudo apt install ffmpeg"
        )


# ── main public API ──────────────────────────────────────────────────

async def handle_voice_message(
    sender: str,
    media_id: str,
    whatsapp_number: str | None = None,
) -> str:
    """Process an incoming WhatsApp voice message.

    Flow
    ----
    1. Download audio from WhatsApp (OGG/Opus).
    2. Convert to 24 kHz int16 numpy array.
    3. Transcribe via Groq Whisper STT.
    4. Run transcribed text through the agent.
    5. Send the agent's text reply back via WhatsApp.

    Parameters
    ----------
    sender : str
        The WhatsApp phone number of the sender.
    media_id : str
        Media ID of the incoming audio (from webhook payload).
    whatsapp_number : str | None
        Alias for sender (for agent context). Defaults to ``sender``.

    Returns
    -------
    str
        The agent's text reply (for logging / storage).
    """
    from agents.voice import AudioInput, STTModelSettings
    from services.ai_services.agent import get_agent_response
    from services.whatsapp import send_text_message

    phone = whatsapp_number or sender

    # ── 1. Download audio ────────────────────────────────────────
    print(f">>> [Voice] Downloading audio {media_id} from WhatsApp …")
    audio_bytes = await download_whatsapp_media(media_id)
    print(f">>> [Voice] Downloaded {len(audio_bytes)} bytes")

    # ── 2. Convert OGG → numpy ───────────────────────────────────
    audio_np = _ogg_bytes_to_numpy(audio_bytes)
    print(f">>> [Voice] Converted to numpy: {len(audio_np)} samples")

    # ── 3. Transcribe (Groq Whisper) ─────────────────────────────
    stt = _get_stt()
    transcript = await stt.transcribe(
        input=AudioInput(buffer=audio_np),
        settings=STTModelSettings(),
        trace_include_sensitive_data=False,
        trace_include_sensitive_audio_data=False,
    )
    print(f">>> [Voice] Transcript: {transcript!r}")

    if not transcript.strip():
        await send_text_message(
            to=sender,
            body="I couldn't hear anything in your voice message. Could you try again? 🎤",
        )
        return ""

    # ── 4. Get agent reply ───────────────────────────────────────
    reply_text = await get_agent_response(
        user_message=transcript,
        whatsapp_number=phone,
    )
    print(f">>> [Voice] Agent reply: {reply_text!r}")

    # ── 5. Send text reply ───────────────────────────────────────
    await send_text_message(to=sender, body=reply_text)
    print(f">>> [Voice] Sent text reply to {sender}")

    return reply_text
