"""Inbound WhatsApp chat message processing (007 US2 / FR-005a).

One entry point, ``process_message``, run as a fire-and-forget background task per inbound
message (the webhook has already acked 200 to Meta). It dedupes by wamid, turns the message
into guest text, runs one agent turn on the ``whatsapp`` channel, and sends the reply back
as freeform text — always inside the 24h customer-service window because it replies to an
inbound message. Contract: specs/007-cancel-message-room-select/contracts/
whatsapp-messages-webhook.md.
"""

from __future__ import annotations

import logging

from app.agent.service import run_turn
from app.db import inbound_messages
from app.db.documents import RoomType
from app.services.whatsapp_messages import send_text

logger = logging.getLogger(__name__)

_ROOM_TYPE_PREFIX = "room_type:"

_UNSUPPORTED_REPLY = (
    "Sorry, I can only read text messages right now. "
    "Please type your question about rooms or bookings."
)


def extract_guest_text(message: dict) -> str | None:
    """Map an inbound message object to the guest's text, or ``None`` if unsupported.

    - ``text`` → the body.
    - ``interactive.list_reply`` → the ``room_type:<canonical>`` row id becomes the bare
      canonical type (FR-007); any other row id falls back to the visible title.
    - anything else (image, audio, reaction, …) → ``None``.
    """

    if message.get("type") == "text":
        body = (message.get("text") or {}).get("body", "")
        return body.strip() or None

    if message.get("type") == "interactive":
        interactive = message.get("interactive") or {}
        if interactive.get("type") != "list_reply":
            return None
        reply = interactive.get("list_reply") or {}
        row_id = reply.get("id") or ""
        if row_id.startswith(_ROOM_TYPE_PREFIX):
            candidate = row_id[len(_ROOM_TYPE_PREFIX):].strip().lower()
            try:
                return RoomType(candidate).value
            except ValueError:
                logger.warning("whatsapp chat: unknown room_type row id %r", row_id)
        title = (reply.get("title") or "").strip()
        return title or None

    return None


async def process_message(message: dict, display_phone_number: str) -> None:
    """Process one inbound message end to end; never raises (webhook already acked)."""

    wamid = message.get("id") or ""
    sender = message.get("from") or ""
    message_type = message.get("type") or ""
    if not wamid or not sender:
        logger.warning("whatsapp chat: dropping message without id/from (type=%r)", message_type)
        return

    try:
        # Redis fast path + durable Mongo backstop; only the caller that passes BOTH
        # processes the message (Meta redelivers webhooks).
        if await inbound_messages.is_duplicate(wamid):
            return
        if await inbound_messages.record(
            wamid=wamid, sender=sender, message_type=message_type
        ) is None:
            return

        text = extract_guest_text(message)
        if text is None:
            await send_text(sender, _UNSUPPORTED_REPLY)
            return

        logger.info(
            "whatsapp chat: turn start wamid=%s sender=%s business=%s",
            wamid, sender, display_phone_number,
        )
        reply, _ = await run_turn(
            text,
            phone_number=sender,
            conversation_id=sender,
            channel="whatsapp",
            business_number=display_phone_number,
        )
        if reply and reply.strip():
            await send_text(sender, reply.strip())
    except Exception:  # noqa: BLE001 - background task: log, never propagate
        logger.exception(
            "whatsapp chat: failed processing wamid=%s sender=%s", wamid, sender
        )
