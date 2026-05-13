"""Shared webhook handler — used by both / and /whatsapp/webhook routes.

Separated into its own module to avoid circular imports between
main.py ↔ endpoints/whatsapp.py.

All incoming WhatsApp messages (voice or text) go through a single
unified pipeline:
  voice  → STT (Groq Whisper) → transcript
  text   → body as-is
  both   → get_agent_response(input, channel="whatsapp")
         → agent replies with optional [SEND_VOICE] prefix
         → send voice note + text  OR  text only
"""

from __future__ import annotations

import asyncio
import time
import traceback
from collections import OrderedDict

from config import WHATSAPP_PHONE_NUMBER_ID
from services.whatsapp import send_text_message, send_voice_note, _save_message
from services.ai_services.agent import get_agent_response

# ── Deduplication cache ──────────────────────────────────────────────
_SEEN_MSG_TTL = 300
_SEEN_MSG_MAX = 5_000
_seen_messages: OrderedDict[str, float] = OrderedDict()

# ── Voice delivery ───────────────────────────────────────────────────
_SEND_VOICE_TAG = "[SEND_VOICE]"
_FEMALE_VOICE = "nova"

_MISSED_CALL_REPLY = (
    "Hi! You just called The Grand Dine 📞\n\n"
    "I'm here to help! Send me a text or voice *message* and I'll assist you "
    "with reservations, menus, or anything else. 🍽️"
)


# ── Helpers ──────────────────────────────────────────────────────────

def _is_duplicate(wa_message_id: str) -> bool:
    if not wa_message_id:
        return False
    now = time.time()
    while _seen_messages:
        oldest_key, oldest_ts = next(iter(_seen_messages.items()))
        if now - oldest_ts > _SEEN_MSG_TTL:
            _seen_messages.pop(oldest_key)
        else:
            break
    if wa_message_id in _seen_messages:
        return True
    if len(_seen_messages) >= _SEEN_MSG_MAX:
        _seen_messages.popitem(last=False)
    _seen_messages[wa_message_id] = now
    return False


def _strip_voice_tag(text: str) -> tuple[bool, str]:
    """Return (use_voice, clean_text) — strips [SEND_VOICE] prefix if present."""
    if text.startswith(_SEND_VOICE_TAG):
        return True, text[len(_SEND_VOICE_TAG):].lstrip()
    return False, text


async def _transcribe(media_id: str) -> str:
    """Download WhatsApp voice message and return its transcript."""
    from services.voice_whatsapp import transcribe_voice_message
    return await transcribe_voice_message(media_id)


# ── Unified message processor ────────────────────────────────────────

async def _process_message(
    source: str,
    sender: str,
    body: str,
    msg_type: str,
    media_id: str,
    call_status: str = "",
) -> None:
    """Process a single incoming message (runs as a background task).

    Voice and text messages follow the same agent pipeline — the session
    (keyed by sender phone number) is shared, so conversation history is
    preserved across voice and text exchanges.
    """
    try:
        # ── Missed call — text reply only ─────────────────────────────
        if msg_type == "call":
            print(f">>> [{source}] Missed call from {sender} (status={call_status!r})")
            try:
                await send_text_message(to=sender, body=_MISSED_CALL_REPLY)
            except Exception as e:
                print(f">>> [{source}] Failed to send missed-call reply: {e}")
            return

        # ── Determine user input text ─────────────────────────────────
        if msg_type == "audio" and media_id:
            print(f">>> [{source}] Transcribing voice message from {sender} …")
            try:
                user_input = await _transcribe(media_id)
                print(f">>> [{source}] Transcript: {user_input!r}")
            except Exception as e:
                print(f">>> [{source}] STT error: {e}")
                traceback.print_exc()
                try:
                    await send_text_message(
                        to=sender,
                        body="Sorry, I couldn't process your voice message. Could you type instead? 🙏",
                    )
                except Exception:
                    pass
                return

            if not user_input.strip():
                try:
                    await send_text_message(
                        to=sender,
                        body="I couldn't hear anything in your voice message. Try again? 🎤",
                    )
                except Exception:
                    pass
                return

        elif body:
            user_input = body
        else:
            return

        # ── Unified agent call ────────────────────────────────────────
        print(f">>> [{source}] Agent processing for {sender}: {user_input[:80]!r}")
        try:
            raw_reply = await get_agent_response(
                user_message=user_input,
                whatsapp_number=sender,
                channel="whatsapp",
            )
        except Exception as e:
            print(f">>> [{source}] Agent error: {e}")
            traceback.print_exc()
            raw_reply = "Sorry, I'm having trouble right now. Please try again! 🙏"

        if not raw_reply or not raw_reply.strip():
            raw_reply = "I'm sorry, I couldn't process that. Please try again! 🙏"

        # ── Agent decides format ──────────────────────────────────────
        use_voice, reply = _strip_voice_tag(raw_reply)
        # Only honor [SEND_VOICE] when the user actually sent a voice message.
        # Text-in → always text-out, even if the agent prefixed [SEND_VOICE].
        if use_voice and msg_type != "audio":
            use_voice = False
        print(f">>> [{source}] use_voice={use_voice} reply={reply[:80]!r}")

        if use_voice:
            # Voice-in → voice-out only. Fall back to text if TTS/upload fails.
            try:
                await send_voice_note(to=sender, text=reply, voice=_FEMALE_VOICE)
                print(f">>> [{source}] Sent voice note to {sender}")
            except Exception as ve:
                print(f">>> [{source}] Voice note failed ({ve}), falling back to text")
                try:
                    await send_text_message(to=sender, body=reply)
                except Exception as e:
                    print(f">>> [{source}] Failed to send fallback text to {sender}: {e}")
        else:
            try:
                await send_text_message(to=sender, body=reply)
                print(f">>> [{source}] Replied to {sender}: {reply[:80]!r}")
            except Exception as e:
                print(f">>> [{source}] Failed to reply to {sender}: {e}")
                traceback.print_exc()

    except Exception as e:
        print(f">>> [{source}] UNHANDLED ERROR in background task: {e}")
        traceback.print_exc()


# ── Main webhook entry point ─────────────────────────────────────────

async def handle_webhook(payload: dict, source: str) -> None:
    """Parse Meta webhook payload and dispatch processing in background."""
    import json as _json

    entries = payload.get("entry", [])
    if not entries:
        return

    for entry in entries:
        for change in entry.get("changes", []):
            field = change.get("field", "")
            value = change.get("value", {})

            # Live calling: SDP offer + ringing/terminate events from Meta.
            if field == "calls":
                from services.wa_calling import handle_call_event

                for call_data in value.get("calls", []):
                    caller = call_data.get("from", "")
                    print(
                        f">>> [{source}] Call event {call_data.get('event', '?')!r} "
                        f"from {caller} id={call_data.get('id', '')[:30]}"
                    )
                    asyncio.create_task(handle_call_event(caller, call_data))
                continue

            if field != "messages":
                print(f">>> [{source}] Skipping webhook field={field!r} value={_json.dumps(value)[:300]}")
                continue

            messages = value.get("messages", [])
            if not messages:
                continue

            for msg in messages:
                sender = msg.get("from", "")
                wa_message_id = msg.get("id", "")
                msg_type = msg.get("type", "text")

                if _is_duplicate(wa_message_id):
                    print(f">>> [{source}] DUPLICATE {wa_message_id} — skipping")
                    continue

                body = ""
                media_id = ""
                call_status = ""

                if msg_type == "text":
                    body = msg.get("text", {}).get("body", "")
                elif msg_type == "audio":
                    media_id = msg.get("audio", {}).get("id", "")
                    body = "[voice message]"
                elif msg_type in ("image", "video", "document"):
                    body = msg.get(msg_type, {}).get("caption", f"[{msg_type}]")
                elif msg_type == "call":
                    call_info = msg.get("call", {})
                    call_status = call_info.get("offer_status", "missed")
                    body = f"[call:{call_status}]"
                elif msg_type == "system":
                    sys_info = msg.get("system", {})
                    sys_type = sys_info.get("type", "")
                    body_text = sys_info.get("body", "")
                    print(f">>> [{source}] System message: type={sys_type!r} body={body_text!r}")
                    if "call" in sys_type.lower() or "call" in body_text.lower():
                        call_status = sys_type
                        body = f"[call:{sys_type}]"
                        msg_type = "call"
                    else:
                        print(f">>> [{source}] Unhandled system message — skipping")
                        continue
                else:
                    print(f">>> [{source}] Unknown msg type={msg_type!r} raw={_json.dumps(msg)[:400]}")
                    continue

                print(f">>> [{source}] From {sender}: '{body}' (type={msg_type})")

                _save_message(
                    sender=sender,
                    recipient=WHATSAPP_PHONE_NUMBER_ID,
                    message_body=body,
                    message_type=msg_type,
                    direction="incoming",
                    wa_message_id=wa_message_id,
                    status="received",
                )

                asyncio.create_task(
                    _process_message(source, sender, body, msg_type, media_id, call_status)
                )
