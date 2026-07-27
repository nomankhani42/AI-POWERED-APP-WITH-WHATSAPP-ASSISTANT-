"""Outbound WhatsApp chat messages via Meta Graph API.

Used for customer-visible booking notifications. Call-control actions live in
``meta_calling.py`` because they use the separate ``/<phone_id>/calls`` endpoint.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings
from app.db.documents import Room, RoomType

logger = logging.getLogger(__name__)

_CAROUSEL_MIN_CARDS = 2
_CAROUSEL_MAX_CARDS = 10


class WhatsAppMessageError(Exception):
    """Raised when Meta rejects or fails an outbound WhatsApp message send."""


async def _post_message(
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    url = (
        f"https://graph.facebook.com/{settings.graph_api_version}"
        f"/{settings.whatsapp_phone_id}/messages"
    )
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json",
    }

    async def _send(active_client: httpx.AsyncClient) -> httpx.Response:
        try:
            return await active_client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            recipient = payload.get("to", "")
            raise WhatsAppMessageError(
                f"Graph message send failed for recipient={recipient}: {exc}"
            ) from exc

    if client is None:
        async with httpx.AsyncClient(timeout=10.0) as owned_client:
            response = await _send(owned_client)
    else:
        response = await _send(client)

    if response.is_error:
        detail = response.text
        logger.error(
            "Graph message send rejected for recipient=%s: HTTP %s -> %s",
            payload.get("to", ""),
            response.status_code,
            detail,
        )
        raise WhatsAppMessageError(
            "Graph message send failed for "
            f"recipient={payload.get('to', '')}: HTTP {response.status_code} {detail}"
        )

    return response.json()


async def send_text(
    to: str,
    body: str,
    *,
    preview_url: bool = False,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Send a freeform WhatsApp text message inside the customer-service window."""

    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body, "preview_url": preview_url},
    }
    return await _post_message(payload, client=client)


async def download_media(
    media_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    """Download an inbound media object (e.g. a voice note) by its media id.

    Two Graph calls: resolve ``GET /<media_id>`` to a short-lived download URL, then GET
    that URL (both require the app's Bearer token — the URL host is ``lookaside.fbsbx.com``).
    Returns ``(content_bytes, mime_type)``.
    """

    settings = get_settings()
    base = f"https://graph.facebook.com/{settings.graph_api_version}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}

    async def _fetch(active: httpx.AsyncClient) -> tuple[bytes, str]:
        meta_resp = await active.get(f"{base}/{media_id}", headers=headers)
        if meta_resp.is_error:
            raise WhatsAppMessageError(
                f"media metadata fetch failed for {media_id}: "
                f"HTTP {meta_resp.status_code} {meta_resp.text}"
            )
        meta = meta_resp.json()
        url = meta.get("url")
        mime = meta.get("mime_type", "")
        if not url:
            raise WhatsAppMessageError(f"media {media_id} had no download url")
        content_resp = await active.get(url, headers=headers, follow_redirects=True)
        if content_resp.is_error:
            raise WhatsAppMessageError(
                f"media download failed for {media_id}: HTTP {content_resp.status_code}"
            )
        return content_resp.content, mime

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=30.0) as owned:
                return await _fetch(owned)
        return await _fetch(client)
    except httpx.HTTPError as exc:
        raise WhatsAppMessageError(f"media download failed for {media_id}: {exc}") from exc


async def upload_media(
    content: bytes,
    mime_type: str,
    *,
    filename: str = "reply.ogg",
    client: httpx.AsyncClient | None = None,
) -> str:
    """Upload media bytes to ``/<phone_id>/media`` and return the reusable media id."""

    settings = get_settings()
    url = (
        f"https://graph.facebook.com/{settings.graph_api_version}"
        f"/{settings.whatsapp_phone_id}/media"
    )
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    files = {"file": (filename, content, mime_type)}
    data = {"messaging_product": "whatsapp", "type": mime_type}

    async def _upload(active: httpx.AsyncClient) -> str:
        resp = await active.post(url, headers=headers, data=data, files=files)
        if resp.is_error:
            raise WhatsAppMessageError(
                f"media upload failed: HTTP {resp.status_code} {resp.text}"
            )
        media_id = resp.json().get("id")
        if not media_id:
            raise WhatsAppMessageError("media upload returned no id")
        return media_id

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=30.0) as owned:
                return await _upload(owned)
        return await _upload(client)
    except httpx.HTTPError as exc:
        raise WhatsAppMessageError(f"media upload failed: {exc}") from exc


async def send_audio(
    to: str,
    media_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Send an uploaded audio media object back to the guest as a voice reply."""

    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "audio",
        "audio": {"id": media_id},
    }
    return await _post_message(payload, client=client)


async def send_room_type_list(
    to: str,
    room_types: Mapping[RoomType, int],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Send the room-type options as a tappable interactive list (007 FR-005).

    ``room_types`` maps each offered canonical type to its sleeps-capacity, in presentation
    order. Meta limits: ≤10 rows total, button ≤20 chars, row title ≤24, description ≤72,
    row id ≤200 — the 8-type canonical catalog always fits one section. A tap comes back on
    the webhook as ``interactive.list_reply`` with our ``room_type:<canonical>`` row id.
    """

    rows = [
        {
            "id": f"room_type:{room_type.value}",
            "title": room_type.value.capitalize(),
            "description": f"sleeps {capacity}",
        }
        for room_type, capacity in room_types.items()
    ]
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": "Which room type would you like?"},
            "action": {
                "button": "Room types",
                "sections": [{"title": "Available room types", "rows": rows}],
            },
        },
    }
    return await _post_message(payload, client=client)


def _room_card(index: int, room: Room, bot_digits: str) -> dict[str, Any]:
    prefill = quote(f"Book {room.name}")
    body = f"{room.name} — {room.room_type.value.capitalize()}\nSleeps {room.capacity}."
    return {
        "card_index": index,
        "type": "cta_url",
        "header": {"type": "image", "image": {"link": room.image_url}},
        "body": {"text": body[:160]},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": "Book",
                # Tap-back pattern: cta_url can't post to the webhook directly, so the
                # button opens our own chat with "Book Room 204" pre-typed; the guest
                # sends it and it arrives as a regular text message.
                "url": f"https://wa.me/{bot_digits}?text={prefill}",
            },
        },
    }


async def send_room_carousel(
    to: str,
    rooms: Sequence[Room],
    bot_display_number: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Send available rooms as a swipeable media carousel (007 extension).

    One bubble, 2–10 ``cta_url`` cards (Meta rejects 1-card carousels — callers fall back
    to text below 2). Each card shows the room photo with a "Book" button that deep-links
    back into this chat with a pre-filled booking message. Rooms without an ``image_url``
    must be filtered out by the caller; freeform carousels require the open 24h window.
    """

    cards = [r for r in rooms if r.image_url][:_CAROUSEL_MAX_CARDS]
    if len(cards) < _CAROUSEL_MIN_CARDS:
        raise ValueError("carousel needs at least 2 rooms with images")

    bot_digits = re.sub(r"\D", "", bot_display_number)
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "carousel",
            "body": {"text": "Here are the available rooms — swipe to browse, tap Book ➜"},
            "action": {
                "cards": [_room_card(i, room, bot_digits) for i, room in enumerate(cards)]
            },
        },
    }
    return await _post_message(payload, client=client)


def booking_confirmation_text(booking: Any) -> str:
    return (
        "Your booking is confirmed.\n"
        f"Reference: {booking.reference}\n"
        f"Room: {booking.room_name}\n"
        f"Dates: {booking.check_in} to {booking.check_out}"
    )


def booking_cancellation_text(booking: Any) -> str:
    return (
        "Your booking has been cancelled.\n"
        f"Reference: {booking.reference}\n"
        f"Room: {booking.room_name}\n"
        f"Dates: {booking.check_in} to {booking.check_out}"
    )


async def send_booking_confirmation(to: str, booking: Any) -> dict[str, Any]:
    """Notify a guest that a booking succeeded."""

    return await send_text(to, booking_confirmation_text(booking))


async def send_booking_cancellation(to: str, booking: Any) -> dict[str, Any]:
    """Notify a guest that their booking was cancelled."""

    return await send_text(to, booking_cancellation_text(booking))
