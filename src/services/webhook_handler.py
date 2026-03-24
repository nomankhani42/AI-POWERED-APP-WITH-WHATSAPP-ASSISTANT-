"""Shared webhook handler — used by both / and /whatsapp/webhook routes.

Separated into its own module to avoid circular imports between
main.py ↔ endpoints/whatsapp.py.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from collections import OrderedDict

from config import WHATSAPP_PHONE_NUMBER_ID
from services.whatsapp import send_text_message, _save_message
from services.ai_services.agent import get_agent_response
from services.voice_whatsapp import handle_voice_message

# ── Deduplication cache ──────────────────────────────────────────────
_SEEN_MSG_TTL = 300  # seconds to remember a message ID
_SEEN_MSG_MAX = 5_000

_seen_messages: OrderedDict[str, float] = OrderedDict()


def _is_duplicate(wa_message_id: str) -> bool:
    """Return True if this message was already processed."""
    if not wa_message_id:
        return False

    now = time.time()

    # Evict stale entries
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


# ── Background message processing ────────────────────────────────────

async def _process_message(
    source: str,
    sender: str,
    body: str,
    msg_type: str,
    media_id: str,
) -> None:
    """Process a single incoming message (runs as a background task)."""
    try:
        if msg_type == "audio" and media_id:
            try:
                reply = await handle_voice_message(
                    sender=sender,
                    media_id=media_id,
                    whatsapp_number=sender,
                )
                print(f">>> [{source}] Voice replied to {sender}: {reply}")
            except Exception as e:
                print(f">>> [{source}] Voice pipeline error: {e}")
                traceback.print_exc()
                try:
                    await send_text_message(
                        to=sender,
                        body="Sorry, I couldn't process your voice message right now. Could you type your message instead? 🙏",
                    )
                except Exception as send_err:
                    print(f">>> [{source}] FAILED to send fallback text: {send_err}")

        elif body:
            try:
                print(f">>> [{source}] Calling agent for {sender}: {body!r}")
                reply = await get_agent_response(body, whatsapp_number=sender)
                print(f">>> [{source}] Agent returned: {reply!r}")
            except Exception as e:
                print(f">>> [{source}] Agent error: {e}")
                traceback.print_exc()
                reply = "Sorry, I'm having trouble processing your request right now."

            if not reply or not reply.strip():
                reply = "I'm sorry, I couldn't process that. Please try again! 🙏"

            try:
                await send_text_message(to=sender, body=reply)
                print(f">>> [{source}] Replied to {sender}: {reply}")
            except Exception as e:
                print(f">>> [{source}] FAILED to reply to {sender}: {e}")
                traceback.print_exc()
    except Exception as e:
        # Catch-all so background task errors are never silently lost
        print(f">>> [{source}] UNHANDLED ERROR in background task: {e}")
        traceback.print_exc()


# ── Main webhook handler ─────────────────────────────────────────────

async def handle_webhook(payload: dict, source: str) -> None:
    """Parse Meta webhook payload and dispatch processing in background."""
    entries = payload.get("entry", [])
    if not entries:
        return

    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])

            if not messages:
                continue

            for msg in messages:
                sender = msg.get("from", "")
                wa_message_id = msg.get("id", "")
                msg_type = msg.get("type", "text")

                # ── Deduplicate ──
                if _is_duplicate(wa_message_id):
                    print(f">>> [{source}] DUPLICATE {wa_message_id} — skipping")
                    continue

                # ── Extract body / media_id ──
                body = ""
                media_id = ""
                if msg_type == "text":
                    body = msg.get("text", {}).get("body", "")
                elif msg_type == "audio":
                    media_id = msg.get("audio", {}).get("id", "")
                    body = "[voice message]"
                elif msg_type in ("image", "video", "document"):
                    body = msg.get(msg_type, {}).get("caption", f"[{msg_type}]")

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

                # ── Fire-and-forget background task ──
                asyncio.create_task(
                    _process_message(source, sender, body, msg_type, media_id)
                )
