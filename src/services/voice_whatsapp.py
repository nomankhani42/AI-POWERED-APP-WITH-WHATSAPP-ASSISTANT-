"""Voice utilities — STT helpers for WhatsApp voice messages.

Public API
----------
download_whatsapp_media(media_id) -> bytes
    Download raw audio bytes from the WhatsApp Cloud API.

transcribe_voice_message(media_id) -> str
    Full pipeline: download → decode OGG → Groq Whisper STT → transcript.
"""

from __future__ import annotations

import io

import httpx
import numpy as np

from config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_API_BASE

# ── STT singleton ────────────────────────────────────────────────────

_stt_model = None


def _get_stt():
    """Return (and cache) an STTModel singleton."""
    global _stt_model
    if _stt_model is None:
        from services.ai_services.part2_stt import create_stt_model
        _stt_model = create_stt_model()
    return _stt_model


# ── WhatsApp media helpers ───────────────────────────────────────────

def _wa_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}


async def download_whatsapp_media(media_id: str) -> bytes:
    """Download a media file from the WhatsApp Cloud API.

    Steps (per Meta docs):
    1. GET /{media_id} → JSON with a ``url`` field.
    2. GET the ``url`` with auth header → binary content.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        meta_resp = await client.get(
            f"{WHATSAPP_API_BASE}/{media_id}",
            headers=_wa_headers(),
        )
        meta_resp.raise_for_status()
        media_url = meta_resp.json()["url"]

        dl_resp = await client.get(media_url, headers=_wa_headers())
        dl_resp.raise_for_status()
        return dl_resp.content


# ── Audio conversion ─────────────────────────────────────────────────

def _ogg_bytes_to_numpy(audio_bytes: bytes) -> np.ndarray:
    """Decode OGG/Opus WhatsApp audio and preprocess it for STT.

    Converts to 16 kHz int16 mono (Whisper native rate), normalises volume,
    removes sub-80 Hz rumble, and strips leading/trailing silence.
    """
    try:
        from pydub import AudioSegment
        from pydub.effects import normalize, high_pass_filter, strip_silence
        segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
        segment = segment.set_channels(1).set_frame_rate(16_000).set_sample_width(2)
        segment = normalize(segment, headroom=0.1)
        segment = high_pass_filter(segment, cutoff=80)
        segment = strip_silence(segment, silence_len=300, silence_thresh=-40, padding=50)
        return np.frombuffer(segment.raw_data, dtype=np.int16)
    except ImportError:
        raise ImportError(
            "pydub is required to decode WhatsApp OGG audio. "
            "Install it:  pip install pydub\n"
            "Also install ffmpeg:  sudo apt install ffmpeg"
        )


# ── Public transcription helper ──────────────────────────────────────

async def transcribe_voice_message(media_id: str) -> str:
    """Download a WhatsApp voice message and return its transcript.

    Parameters
    ----------
    media_id : str
        The media ID from the incoming webhook payload (``msg["audio"]["id"]``).

    Returns
    -------
    str
        Transcript text (may be empty if audio was silent or inaudible).
    """
    from agents.voice import AudioInput, STTModelSettings

    audio_bytes = await download_whatsapp_media(media_id)
    audio_np = _ogg_bytes_to_numpy(audio_bytes)

    stt = _get_stt()
    return await stt.transcribe(
        input=AudioInput(buffer=audio_np, frame_rate=16_000),
        settings=STTModelSettings(),
        trace_include_sensitive_data=False,
        trace_include_sensitive_audio_data=False,
    )
