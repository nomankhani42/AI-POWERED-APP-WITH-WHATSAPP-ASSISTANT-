"""Unit tests for outbound WhatsApp chat message helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import whatsapp_messages


async def test_send_text_posts_canonical_graph_payload() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("Authorization"),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            json={
                "messaging_product": "whatsapp",
                "contacts": [{"input": "+15550001111", "wa_id": "15550001111"}],
                "messages": [{"id": "wamid.test", "message_status": "accepted"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await whatsapp_messages.send_text(
            "+15550001111",
            "Hello!",
            client=client,
        )

    assert response["messages"][0]["message_status"] == "accepted"
    assert requests == [
        {
            "url": "https://graph.facebook.com/v21.0/test-phone-id/messages",
            "authorization": "Bearer test-whatsapp-token",
            "body": {
                "messaging_product": "whatsapp",
                "to": "+15550001111",
                "type": "text",
                "text": {"body": "Hello!", "preview_url": False},
            },
        }
    ]


async def test_send_text_raises_on_graph_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad recipient"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(whatsapp_messages.WhatsAppMessageError):
            await whatsapp_messages.send_text("+15550001111", "Hello!", client=client)


def test_booking_notification_texts_include_guest_details() -> None:
    booking = SimpleNamespace(
        reference="BK-1234ABCD",
        room_name="Room 12",
        check_in="2026-08-01",
        check_out="2026-08-03",
    )

    confirmation = whatsapp_messages.booking_confirmation_text(booking)
    cancellation = whatsapp_messages.booking_cancellation_text(booking)

    assert "Your booking is confirmed." in confirmation
    assert "Reference: BK-1234ABCD" in confirmation
    assert "Room: Room 12" in cancellation
    assert "Dates: 2026-08-01 to 2026-08-03" in cancellation
