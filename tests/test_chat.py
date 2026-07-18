"""POST /chat contract test with the agent turn stubbed (T017)."""

import app.api.routes.chat as chat_route


def test_chat_returns_reply_and_conversation_id(client, monkeypatch) -> None:
    async def fake_run_turn(message, phone_number, conversation_id=None):
        assert message == "Any rooms free?"
        assert phone_number == "+15551234567"
        return "Yes, Room 12 is free.", conversation_id or phone_number

    monkeypatch.setattr(chat_route, "run_turn", fake_run_turn)

    resp = client.post(
        "/chat",
        json={"message": "Any rooms free?", "phone_number": "+15551234567"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "reply": "Yes, Room 12 is free.",
        "conversation_id": "+15551234567",
    }


def test_chat_requires_message_and_phone(client) -> None:
    resp = client.post("/chat", json={"message": "hi"})
    assert resp.status_code == 422
