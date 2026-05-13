"""Mobile chat endpoint — text + voice + WebSocket streaming with the Grand Dine AI agent."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import traceback
import wave

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from services.ai_services.agent import get_agent_response, get_agent_response_stream

router = APIRouter(prefix="/chat", tags=["Chat"])

_SEND_VOICE_TAG = "[SEND_VOICE]"
_FEMALE_VOICE = "nova"


def _clean_reply(text: str) -> str:
    """Strip [SEND_VOICE] prefix if present (safety net for app channel)."""
    if text.startswith(_SEND_VOICE_TAG):
        return text[len(_SEND_VOICE_TAG):].lstrip()
    return text


# ---------------------------------------------------------------------------
# TTS helper — converts text to base64-encoded WAV
# ---------------------------------------------------------------------------

async def _text_to_speech_b64(text: str) -> str | None:
    """Generate TTS audio (female voice) and return as base64-encoded WAV.

    Returns None if TTS is unavailable or fails.
    """
    try:
        from services.ai_services.tts_openai import synthesise_to_wav_b64
        return await synthesise_to_wav_b64(text, voice=_FEMALE_VOICE)
    except Exception as e:
        print(f">>> [TTS] Error generating speech: {e}")
        return None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Body for the mobile chat endpoint."""
    user_id: str          # Unique identifier (phone number or user ID)
    message: str          # User's text message
    full_name: str = ""   # Pre-filled from app registration
    email: str = ""       # Pre-filled from app registration


# ---------------------------------------------------------------------------
# Simple (non-streaming) endpoint
# ---------------------------------------------------------------------------

@router.post("")
async def chat(req: ChatRequest):
    """Return agent response as a simple JSON object.

    Returns ``{"reply": "..."}`` — no SSE, no streaming complexity.
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    reply = await get_agent_response(
        user_message=req.message,
        whatsapp_number=req.user_id,
        channel="app",
        full_name=req.full_name,
        email=req.email,
    )
    return {"reply": _clean_reply(reply)}


# ---------------------------------------------------------------------------
# WebSocket streaming endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    """Stream agent responses over a persistent WebSocket connection.

    Protocol
    --------
    Client sends JSON messages:
        {"type": "message", "user_id": "...", "message": "...", "full_name": "...", "email": "..."}

    Server sends JSON messages:
        {"type": "token",  "data": "<text delta>"}     — incremental token
        {"type": "done",   "data": "<full reply>"}      — final complete reply
        {"type": "error",  "data": "<error message>"}   — on failure

    The connection stays open for multiple exchanges — client can send
    a new ``message`` as soon as it receives ``done`` or ``error``.
    """
    await ws.accept()
    print(">>> [WS] Client connected")

    try:
        while True:
            # Wait for a message from the client
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "data": "Invalid JSON"})
                continue

            msg_type = payload.get("type", "message")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            user_id = payload.get("user_id", "app-user")
            message = payload.get("message", "").strip()
            full_name = payload.get("full_name", "")
            email = payload.get("email", "")

            if not message:
                await ws.send_json({"type": "error", "data": "Empty message"})
                continue

            # Stream the agent response token-by-token
            full_parts: list[str] = []
            try:
                async for delta in get_agent_response_stream(
                    user_message=message,
                    whatsapp_number=user_id,
                    channel="app",
                    full_name=full_name,
                    email=email,
                ):
                    full_parts.append(delta)
                    await ws.send_json({"type": "token", "data": delta})

                full_reply = _clean_reply("".join(full_parts))
                if not full_reply.strip():
                    full_reply = "Sorry, couldn't generate a response. Please try again! 🙏"
                await ws.send_json({"type": "done", "data": full_reply})

            except Exception as exc:
                traceback.print_exc()
                await ws.send_json({"type": "error", "data": str(exc)})

    except WebSocketDisconnect:
        print(">>> [WS] Client disconnected")
    except Exception as e:
        print(f">>> [WS] Unexpected error: {e}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Voice endpoint — receives audio file, transcribes, runs agent
# ---------------------------------------------------------------------------

@router.post("/voice")
async def voice_chat(
    audio: UploadFile = File(...),
    user_id: str = Form("app-user"),
    full_name: str = Form(""),
    email: str = Form(""),
):
    """Accept a voice recording, transcribe it, and return the agent reply.

    The mobile app records audio (m4a/wav), uploads it as multipart,
    and receives ``{"transcript": "...", "reply": "..."}``.
    """
    import numpy as np
    from pydub import AudioSegment
    from pydub.effects import normalize, high_pass_filter, strip_silence
    from agents.voice import AudioInput, STTModelSettings

    # Lazy-load STT model
    from services.voice_whatsapp import _get_stt

    # ── 1. Read uploaded audio ───────────────────────────────────
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    print(f">>> [Voice-App] Received {len(audio_bytes)} bytes, content_type={audio.content_type}")

    # ── 2. Decode + preprocess to 16 kHz int16 mono (Whisper native) ─
    try:
        segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
        segment = segment.set_channels(1).set_frame_rate(16_000).set_sample_width(2)
        segment = normalize(segment, headroom=0.1)
        segment = high_pass_filter(segment, cutoff=80)
        segment = strip_silence(segment, silence_len=300, silence_thresh=-40, padding=50)
        audio_np = np.frombuffer(segment.raw_data, dtype=np.int16)
    except Exception as e:
        print(f">>> [Voice-App] Audio decode error: {e}")
        raise HTTPException(status_code=400, detail="Could not decode audio file")

    print(f">>> [Voice-App] Converted to numpy: {len(audio_np)} samples")

    # ── 3. Transcribe ────────────────────────────────────────────
    stt = _get_stt()
    transcript = await stt.transcribe(
        input=AudioInput(buffer=audio_np, frame_rate=16_000),
        settings=STTModelSettings(),
        trace_include_sensitive_data=False,
        trace_include_sensitive_audio_data=False,
    )
    print(f">>> [Voice-App] Transcript: {transcript!r}")

    if not transcript.strip():
        return {"transcript": "", "reply": "I couldn't hear anything. Could you try again? 🎤"}

    # ── 4. Run agent ─────────────────────────────────────────────
    reply = await get_agent_response(
        user_message=transcript,
        whatsapp_number=user_id,
        channel="app",
        full_name=full_name,
        email=email,
    )
    reply = _clean_reply(reply)
    print(f">>> [Voice-App] Agent reply: {reply!r}")

    # ── 5. Generate TTS audio for the reply (female voice) ───────
    audio_b64 = await _text_to_speech_b64(reply)

    return {"transcript": transcript, "reply": reply, "audio": audio_b64}


# ---------------------------------------------------------------------------
# TTS-only endpoint — convert any text to playable audio
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    text: str

@router.post("/tts")
async def text_to_speech(req: TTSRequest):
    """Convert text to speech and return base64-encoded WAV.

    Returns ``{"audio": "<base64 WAV>"}`` or ``{"audio": null}`` on failure.
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    audio_b64 = await _text_to_speech_b64(req.text)
    return {"audio": audio_b64}
