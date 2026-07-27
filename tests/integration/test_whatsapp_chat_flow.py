"""WhatsApp chat flow integration (007 US2, T011).

Drives ``whatsapp_chat.process_message`` and the interactive-list sender directly with the
Graph transport mocked at ``_post_message``, so the real payload-building code runs without
network. Contract: specs/007-cancel-message-room-select/contracts/room-type-selection.md.
"""

from __future__ import annotations

import pytest

import app.services.whatsapp_chat as chat
from app.db.documents import RoomType
from app.services import whatsapp_messages

SENDER = "923001234567"


class _FakeInbound:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    async def is_duplicate(self, wamid: str, **_: object) -> bool:
        if wamid in self.seen:
            return True
        self.seen.add(wamid)
        return False

    async def record(self, *, wamid: str, sender: str, message_type: str):
        return {"wamid": wamid}


@pytest.fixture
def graph(monkeypatch) -> list[dict]:
    """Capture every outbound Graph payload instead of hitting the network."""

    payloads: list[dict] = []

    async def fake_post_message(payload: dict, *, client=None) -> dict:
        payloads.append(payload)
        return {"messages": [{"id": "wamid.out"}]}

    monkeypatch.setattr(whatsapp_messages, "_post_message", fake_post_message)
    return payloads


@pytest.fixture
def stub_turn(monkeypatch):
    async def fake_run_turn(message, phone_number, conversation_id=None, channel="api", business_number=None):
        return f"echo: {message}", conversation_id or phone_number

    monkeypatch.setattr(chat, "inbound_messages", _FakeInbound())
    monkeypatch.setattr(chat, "run_turn", fake_run_turn)


async def test_inbound_text_produces_text_reply_to_sender(graph, stub_turn) -> None:
    await chat.process_message(
        {"from": SENDER, "id": "wamid.F1", "timestamp": "1752566400", "type": "text",
         "text": {"body": "any rooms tomorrow?"}},
        "15551234567",
    )

    assert len(graph) == 1
    payload = graph[0]
    assert payload["to"] == SENDER
    assert payload["type"] == "text"
    assert payload["text"]["body"] == "echo: any rooms tomorrow?"


async def test_inbound_voice_transcribes_and_replies_with_voice(graph, stub_turn, monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_download_media(media_id, *, client=None):
        calls["download"] = media_id
        return b"OGGaudio", "audio/ogg"

    async def fake_transcribe_file(audio, *, mimetype="audio/ogg", client=None):
        calls["transcribe"] = (audio, mimetype)
        return "any rooms tomorrow?"

    async def fake_synthesize_to_ogg(text, *, client=None):
        calls["synthesize"] = text
        return b"REPLYaudio"

    async def fake_upload_media(content, mime_type, *, filename="reply.ogg", client=None):
        calls["upload"] = (content, mime_type)
        return "media.out.1"

    monkeypatch.setattr(chat, "download_media", fake_download_media)
    monkeypatch.setattr(chat.stt, "transcribe_file", fake_transcribe_file)
    monkeypatch.setattr(chat.tts, "synthesize_to_ogg", fake_synthesize_to_ogg)
    monkeypatch.setattr(chat, "upload_media", fake_upload_media)

    await chat.process_message(
        {"from": SENDER, "id": "wamid.V1", "timestamp": "1752566400", "type": "audio",
         "audio": {"id": "media.in.1", "mime_type": "audio/ogg; codecs=opus", "voice": True}},
        "15551234567",
    )

    # Transcribed the downloaded audio, synthesized the agent reply, replied as a voice note.
    assert calls["download"] == "media.in.1"
    assert calls["synthesize"] == "echo: any rooms tomorrow?"
    assert calls["upload"] == (b"REPLYaudio", "audio/ogg")
    assert len(graph) == 1
    payload = graph[0]
    assert payload["to"] == SENDER
    assert payload["type"] == "audio"
    assert payload["audio"] == {"id": "media.out.1"}


async def test_room_type_list_payload_matches_contract(graph) -> None:
    caps = {RoomType.single: 1, RoomType.deluxe: 2, RoomType.family: 4}
    await whatsapp_messages.send_room_type_list(SENDER, caps)

    assert len(graph) == 1
    payload = graph[0]
    assert payload["to"] == SENDER
    assert payload["type"] == "interactive"
    interactive = payload["interactive"]
    assert interactive["type"] == "list"
    assert interactive["body"]["text"]
    assert len(interactive["action"]["button"]) <= 20

    rows = [row for section in interactive["action"]["sections"] for row in section["rows"]]
    assert [row["id"] for row in rows] == [
        "room_type:single", "room_type:deluxe", "room_type:family",
    ]
    assert len(rows) <= 10
    for row in rows:
        assert len(row["title"]) <= 24
        assert len(row["id"]) <= 200
        assert len(row.get("description", "")) <= 72
    assert rows[0]["description"] == "sleeps 1"


def _imaged_room(name: str, room_type: str = "double", capacity: int = 2):
    from app.db.documents import Room

    return Room(
        name=name, room_type=room_type, capacity=capacity,
        image_url=f"https://images.example.com/{name.replace(' ', '-')}.jpg",
    )


async def test_room_carousel_payload_matches_reference_shape(graph) -> None:
    rooms = [_imaged_room("Room 201"), _imaged_room("Room 204", "deluxe")]
    await whatsapp_messages.send_room_carousel(SENDER, rooms, "+1 555-123-4567")

    assert len(graph) == 1
    payload = graph[0]
    assert payload["to"] == SENDER and payload["type"] == "interactive"
    interactive = payload["interactive"]
    assert interactive["type"] == "carousel"
    assert interactive["body"]["text"]

    cards = interactive["action"]["cards"]
    assert [c["card_index"] for c in cards] == [0, 1]
    for card in cards:
        assert card["type"] == "cta_url"  # all cards same type; cta_url works everywhere
        assert card["header"]["type"] == "image"
        assert card["header"]["image"]["link"].startswith("https://")
        assert len(card["body"]["text"]) <= 160
        params = card["action"]["parameters"]
        assert len(params["display_text"]) <= 20
        # tap-back deep link: digits-only bot number + prefilled Book message
        assert params["url"].startswith("https://wa.me/15551234567?text=Book%20Room")


async def test_room_carousel_caps_at_ten_and_skips_imageless(graph) -> None:
    from app.db.documents import Room

    rooms = [_imaged_room(f"Room {n}") for n in range(12)]
    rooms.insert(0, Room(name="Room X", room_type="suite", capacity=4))  # no image
    await whatsapp_messages.send_room_carousel(SENDER, rooms, "15551234567")

    cards = graph[0]["interactive"]["action"]["cards"]
    assert len(cards) == 10
    assert all("Room X" not in c["body"]["text"] for c in cards)


async def test_room_carousel_rejects_fewer_than_two_cards(graph) -> None:
    with pytest.raises(ValueError):
        await whatsapp_messages.send_room_carousel(
            SENDER, [_imaged_room("Room 201")], "15551234567"
        )
    assert graph == []


async def test_full_catalog_fits_one_list(graph) -> None:
    caps = {t: 2 for t in RoomType}  # all eight canonical types
    await whatsapp_messages.send_room_type_list(SENDER, caps)

    rows = [
        row
        for section in graph[0]["interactive"]["action"]["sections"]
        for row in section["rows"]
    ]
    assert len(rows) == len(RoomType) <= 10
