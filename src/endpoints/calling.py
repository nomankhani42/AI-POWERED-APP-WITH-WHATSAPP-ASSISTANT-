"""Voice calling endpoint.

Provides two interfaces for real-time AI voice calls:

  POST /calling/turn      — single-turn: upload audio → get audio back
  WS   /calling/ws        — persistent WebSocket for a full voice call session

Pipeline per turn
-----------------
  Audio bytes (any format)
  → pydub decode → 24 kHz int16 mono numpy
  → Groq Whisper STT            (transcription)
  → Grand Dine Agent (gpt-4o-mini) (text reply)
  → OpenAI gpt-4o-mini-tts      (speech synthesis)
  → base64 WAV (HTTP) / binary chunks (WebSocket)

WebSocket protocol
------------------
Client → Server (JSON):
  {"type": "start",       "user_id": "...", "full_name": "...", "email": "..."}
  {"type": "audio_chunk", "data": "<base64 audio bytes>", "format": "wav|mp3|..."}
  {"type": "end_of_speech"}
  {"type": "ping"}

Server → Client (JSON):
  {"type": "ready"}
  {"type": "transcript",      "data": "...", "final": true}
  {"type": "reply_text_delta","data": "..."}                     # streamed tokens
  {"type": "reply_text",      "data": "..."}                     # full text once done
  {"type": "audio_chunk",     "data": "<base64 WAV>", "index": N}# one WAV per sentence
  {"type": "audio_done"}
  {"type": "error",           "data": "..."}
  {"type": "pong"}

Long replies are split into sentences; each sentence is synthesised to
a self-contained WAV and shipped as soon as it's ready, so the client
starts playing within ~1 s instead of waiting for the full reply.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import traceback
import uuid

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydub import AudioSegment

from services.ai_services.agent import get_agent_response, get_agent_response_stream
from services.ai_services.upstash_memory import get_session

router = APIRouter(prefix="/calling", tags=["Calling"])

# Sentence boundary: any of . ! ? … followed by whitespace/end, or a newline.
# Used to flush the streaming agent text into TTS-sized utterances.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[\.!\?…])\s+|\n+")
_MIN_SENTENCE_CHARS = 18  # don't flush tiny fragments like "Sure." on their own

# ── audio helpers ─────────────────────────────────────────────────────

def _preprocess_segment(segment: "AudioSegment") -> np.ndarray:
    """Convert an AudioSegment to 16 kHz int16 mono numpy, with STT-optimised preprocessing.

    Chain: resample → normalise → high-pass 80 Hz → strip edge-silence.
    16 kHz is Whisper's native rate (smaller payload, full quality).
    Normalisation fixes quiet mobile recordings.
    High-pass at 80 Hz removes HVAC/wind/handling rumble.
    Edge-silence stripping avoids billing for dead air.
    """
    from pydub.effects import normalize, high_pass_filter, strip_silence

    segment = segment.set_channels(1).set_frame_rate(16_000).set_sample_width(2)
    segment = normalize(segment, headroom=0.1)
    segment = high_pass_filter(segment, cutoff=80)
    segment = strip_silence(segment, silence_len=300, silence_thresh=-40, padding=50)
    return np.frombuffer(segment.raw_data, dtype=np.int16)


def _preprocess_for_stt(audio_bytes: bytes) -> np.ndarray:
    """Decode a single audio file and preprocess it for STT."""
    segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
    return _preprocess_segment(segment)


def _preprocess_chunks_for_stt(chunks: list[bytes]) -> np.ndarray:
    """Decode one or more audio chunks, concatenate, and preprocess for STT.

    Tries to decode the concatenated bytes first (works for streaming WebM/Opus
    from browser MediaRecorder). Falls back to decoding each chunk individually
    and concatenating the AudioSegments (needed for WAV/MP3 multi-file streams).
    """
    if not chunks:
        return np.zeros(0, dtype=np.int16)

    try:
        segment = AudioSegment.from_file(io.BytesIO(b"".join(chunks)))
    except Exception:
        segment = AudioSegment.empty()
        for chunk in chunks:
            try:
                segment += AudioSegment.from_file(io.BytesIO(chunk))
            except Exception:
                pass

    return _preprocess_segment(segment)


def _ensure_preprocess_for_stt(audio_bytes: bytes) -> np.ndarray:
    """Like _preprocess_for_stt but raises HTTP 400 on decode failure."""
    try:
        return _preprocess_for_stt(audio_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not decode audio: {exc}")


# ── STT singleton ─────────────────────────────────────────────────────

_stt_model = None


def _get_stt():
    global _stt_model
    if _stt_model is None:
        from services.ai_services.part2_stt import create_stt_model
        _stt_model = create_stt_model()
    return _stt_model


# ── shared transcription helper ───────────────────────────────────────

async def _transcribe(audio_np: np.ndarray) -> str:
    from agents.voice import AudioInput, STTModelSettings

    stt = _get_stt()
    return await stt.transcribe(
        input=AudioInput(buffer=audio_np, frame_rate=16_000),
        settings=STTModelSettings(),
        trace_include_sensitive_data=False,
        trace_include_sensitive_audio_data=False,
    )


# ── shared TTS helper ─────────────────────────────────────────────────

async def _tts_b64(text: str) -> str | None:
    """Convert text to speech and return base64-encoded WAV, or None on failure."""
    try:
        from services.ai_services.tts_openai import synthesise_to_wav_b64
        return await synthesise_to_wav_b64(text)
    except Exception as exc:
        print(f">>> [Calling/TTS] Error: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────
# POST /calling/turn  — single-turn HTTP endpoint
# ─────────────────────────────────────────────────────────────────────

@router.post("/turn")
async def calling_turn(
    audio: UploadFile = File(...),
    user_id: str = Form("caller"),
    full_name: str = Form(""),
    email: str = Form(""),
):
    """Single-turn voice call: upload speech, receive agent speech back.

    Accepts any audio format (WAV, MP3, OGG, M4A, …) via multipart.

    Returns
    -------
    JSON
        ``{"transcript": str, "reply": str, "audio": str | null}``

        ``audio`` is a base64-encoded WAV (24 kHz, 16-bit, mono) ready
        for direct playback.  ``null`` if TTS fails.
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    print(f">>> [Calling/HTTP] {user_id}: {len(audio_bytes)} bytes, {audio.content_type}")

    # 1. Decode + preprocess audio for STT
    audio_np = _ensure_preprocess_for_stt(audio_bytes)

    # 2. STT — Groq Whisper
    transcript = await _transcribe(audio_np)
    print(f">>> [Calling/HTTP] Transcript: {transcript!r}")

    if not transcript.strip():
        return {
            "transcript": "",
            "reply": "I couldn't hear anything. Could you try again?",
            "audio": await _tts_b64("I couldn't hear anything. Could you try again?"),
        }

    # 3. Agent — gpt-4o-mini
    # Single-turn call: fresh, throwaway session id per request so no
    # history is carried in from earlier calls or chats.
    reply = await get_agent_response(
        user_message=transcript,
        whatsapp_number=user_id,
        channel="app",
        full_name=full_name,
        email=email,
        session_id=f"call:turn:{uuid.uuid4().hex}",
    )
    print(f">>> [Calling/HTTP] Agent reply: {reply!r}")

    # 4. TTS — gpt-4o-mini-tts
    audio_b64 = await _tts_b64(reply)

    return {"transcript": transcript, "reply": reply, "audio": audio_b64}


# ─────────────────────────────────────────────────────────────────────
# WS /calling/ws  — persistent WebSocket voice call
# ─────────────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def calling_ws(ws: WebSocket):
    """Real-time bidirectional voice call over WebSocket.

    Protocol
    --------
    1. Client sends ``{"type": "start", "user_id": "...", ...}`` to
       open a session.
    2. Client sends one or more ``{"type": "audio_chunk", "data": "..."}``
       messages containing base64-encoded audio bytes.
    3. Client sends ``{"type": "end_of_speech"}`` when done speaking.
    4. Server responds with:
       - ``{"type": "transcript", "data": "..."}``
       - ``{"type": "reply_text", "data": "..."}``
       - One or more ``{"type": "audio_chunk", "data": "...", "index": N}``
       - ``{"type": "audio_done"}``
    5. Repeat from step 2 for the next turn.
    """
    await ws.accept()
    print(">>> [Calling/WS] Client connected")

    user_id = "caller"
    full_name = ""
    email = ""
    audio_buffer: list[bytes] = []
    # Per-connection session id — one fresh Upstash session per WS call,
    # so the agent's short-term memory only covers this single call.
    call_session_id = f"call:ws:{uuid.uuid4().hex}"

    async def send(msg: dict) -> None:
        await ws.send_text(json.dumps(msg))

    try:
        while True:
            raw = await ws.receive_text()

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await send({"type": "error", "data": "Invalid JSON"})
                continue

            msg_type = payload.get("type", "")

            # ── ping ─────────────────────────────────────────────────
            if msg_type == "ping":
                await send({"type": "pong"})
                continue

            # ── start: initialise session ─────────────────────────────
            if msg_type == "start":
                user_id = payload.get("user_id", "caller")
                full_name = payload.get("full_name", "")
                email = payload.get("email", "")
                audio_buffer.clear()
                # New call → new fresh session, even if the WS is reused.
                call_session_id = f"call:ws:{uuid.uuid4().hex}"
                await send({"type": "ready"})
                print(
                    f">>> [Calling/WS] Session started for {user_id} "
                    f"(session={call_session_id})"
                )
                continue

            # ── audio_chunk: accumulate ───────────────────────────────
            if msg_type == "audio_chunk":
                data_b64 = payload.get("data", "")
                if data_b64:
                    try:
                        audio_buffer.append(base64.b64decode(data_b64))
                    except Exception:
                        await send({"type": "error", "data": "Invalid base64 audio"})
                continue

            # ── end_of_speech: process the buffered audio ─────────────
            if msg_type == "end_of_speech":
                if not audio_buffer:
                    await send({"type": "error", "data": "No audio received"})
                    continue

                chunks = list(audio_buffer)
                audio_buffer.clear()

                try:
                    audio_np = _preprocess_chunks_for_stt(chunks)
                except Exception as exc:
                    await send({"type": "error", "data": f"Audio decode error: {exc}"})
                    continue

                try:
                    # STT
                    transcript = await _transcribe(audio_np)
                    print(f">>> [Calling/WS] Transcript: {transcript!r}")
                    await send({"type": "transcript", "data": transcript, "final": True})

                    if not transcript.strip():
                        silence_reply = "I couldn't hear you clearly. Could you speak again?"
                        await send({"type": "reply_text", "data": silence_reply})
                        audio_b64 = await _tts_b64(silence_reply)
                        if audio_b64:
                            await send({"type": "audio_chunk", "data": audio_b64, "index": 0})
                        await send({"type": "audio_done"})
                        continue

                    # Agent — scoped to this call's session only.
                    reply = await get_agent_response(
                        user_message=transcript,
                        whatsapp_number=user_id,
                        channel="app",
                        full_name=full_name,
                        email=email,
                        session_id=call_session_id,
                    )
                    print(f">>> [Calling/WS] Reply: {reply!r}")
                    await send({"type": "reply_text", "data": reply})

                    # TTS — synthesise once and send as a single WAV
                    audio_b64 = await _tts_b64(reply)
                    if audio_b64:
                        await send({"type": "audio_chunk", "data": audio_b64, "index": 0})

                    await send({"type": "audio_done"})

                except Exception as exc:
                    traceback.print_exc()
                    await send({"type": "error", "data": str(exc)})

                continue

            # unknown message type
            await send({"type": "error", "data": f"Unknown message type: {msg_type!r}"})

    except WebSocketDisconnect:
        print(f">>> [Calling/WS] Client disconnected ({user_id})")
    except Exception as exc:
        print(f">>> [Calling/WS] Unexpected error: {exc}")
        traceback.print_exc()
    finally:
        # Drop the per-call session so its history doesn't sit in Redis
        # for the full 1-hour TTL after the call ends.
        try:
            await get_session(call_session_id).clear_session()
        except Exception as exc:
            print(f">>> [Calling/WS] Session clear failed: {exc}")
