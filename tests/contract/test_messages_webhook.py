"""Contract tests for inbound WhatsApp chat messages on the shared webhook (007 US2, T010).

See specs/007-cancel-message-room-select/contracts/whatsapp-messages-webhook.md. No live
DB/Redis/network: the chat service's dedupe store, agent turn, and outbound sender are
patched onto in-memory fakes, and background coroutines captured from ``_fire_and_forget``
are drained with ``asyncio.run`` (they only touch those fakes). Signatures are computed from
the dummy ``WHATSAPP_APP_SECRET`` pinned in ``tests/conftest.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json

import pytest

import app.api.routes.whatsapp_calling as webhook_route
import app.services.whatsapp_chat as chat
from app.core.config import get_settings

SENDER = "923001234567"


def _sign(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _envelope(*messages: dict, extra_value: dict | None = None) -> dict:
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15551234567", "phone_number_id": "phone-1"},
        "messages": list(messages),
    }
    if extra_value:
        value.update(extra_value)
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-1", "changes": [{"field": "messages", "value": value}]}],
    }


def _text_message(body: str, wamid: str = "wamid.T1") -> dict:
    return {"from": SENDER, "id": wamid, "timestamp": "1752566400", "type": "text",
            "text": {"body": body}}


def _list_reply_message(row_id: str, title: str, wamid: str = "wamid.L1") -> dict:
    return {"from": SENDER, "id": wamid, "timestamp": "1752566460", "type": "interactive",
            "interactive": {"type": "list_reply",
                            "list_reply": {"id": row_id, "title": title}}}


def _post(client, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        "/whatsapp/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body, get_settings().whatsapp_app_secret),
        },
    )


class _FakeInbound:
    """In-memory stand-in for ``app.db.inbound_messages``."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.records: list[dict] = []

    async def is_duplicate(self, wamid: str, **_: object) -> bool:
        if wamid in self.seen:
            return True
        self.seen.add(wamid)
        return False

    async def record(self, *, wamid: str, sender: str, message_type: str):
        record = {"wamid": wamid, "sender": sender, "message_type": message_type}
        self.records.append(record)
        return record


@pytest.fixture
def background() -> list:
    return []


@pytest.fixture
def chat_env(monkeypatch, background):
    """Patch the chat service's collaborators and capture background coroutines."""

    fake_inbound = _FakeInbound()
    turns: list[dict] = []
    sends: list[tuple[str, str]] = []

    async def fake_run_turn(message, phone_number, conversation_id=None, channel="api", business_number=None):
        turns.append({"message": message, "phone_number": phone_number,
                      "conversation_id": conversation_id, "channel": channel})
        return "Sure — one moment.", conversation_id or phone_number

    async def fake_send_text(to, body, **_: object):
        sends.append((to, body))
        return {"messages": [{"id": "wamid.out"}]}

    monkeypatch.setattr(chat, "inbound_messages", fake_inbound)
    monkeypatch.setattr(chat, "run_turn", fake_run_turn)
    monkeypatch.setattr(chat, "send_text", fake_send_text)
    monkeypatch.setattr(webhook_route, "_fire_and_forget", background.append)
    return {"inbound": fake_inbound, "turns": turns, "sends": sends}


def _drain(background: list) -> None:
    for coro in background:
        if inspect.iscoroutine(coro):
            asyncio.run(coro)
    background.clear()


def test_text_message_dispatches_one_turn(client, chat_env, background) -> None:
    response = _post(client, _envelope(_text_message("do you have a family room friday?")))

    assert response.status_code == 200
    assert response.json() == {"status": "received"}
    _drain(background)

    assert len(chat_env["turns"]) == 1
    turn = chat_env["turns"][0]
    assert turn["message"] == "do you have a family room friday?"
    assert turn["phone_number"] == SENDER
    assert turn["channel"] == "whatsapp"
    # the agent's reply goes back to the sender
    assert chat_env["sends"] == [(SENDER, "Sure — one moment.")]


def test_duplicate_wamid_processes_once(client, chat_env, background) -> None:
    payload = _envelope(_text_message("hello", wamid="wamid.DUP"))
    assert _post(client, payload).status_code == 200
    assert _post(client, payload).status_code == 200
    _drain(background)

    assert len(chat_env["turns"]) == 1


def test_list_reply_becomes_room_type_answer(client, chat_env, background) -> None:
    payload = _envelope(_list_reply_message("room_type:deluxe", "Deluxe"))
    assert _post(client, payload).status_code == 200
    _drain(background)

    assert len(chat_env["turns"]) == 1
    assert chat_env["turns"][0]["message"] == "deluxe"


def test_unknown_list_reply_id_falls_back_to_title(client, chat_env, background) -> None:
    payload = _envelope(_list_reply_message("something:else", "Garden view"))
    assert _post(client, payload).status_code == 200
    _drain(background)

    assert len(chat_env["turns"]) == 1
    assert chat_env["turns"][0]["message"] == "Garden view"


def test_unsupported_type_gets_polite_reply_and_no_turn(client, chat_env, background) -> None:
    message = {"from": SENDER, "id": "wamid.IMG", "timestamp": "1752566400",
               "type": "image", "image": {"id": "media-1", "mime_type": "image/jpeg"}}
    assert _post(client, _envelope(message)).status_code == 200
    _drain(background)

    assert chat_env["turns"] == []
    assert len(chat_env["sends"]) == 1
    to, body = chat_env["sends"][0]
    assert to == SENDER and body  # brief text-only notice


def test_malformed_payloads_still_ack_200(client, chat_env, background) -> None:
    for payload in (
        {"object": "whatsapp_business_account"},  # no entry
        {"entry": "not-a-list"},
        _envelope({"type": "text"}),  # message missing id/from
    ):
        assert _post(client, payload).status_code == 200
    _drain(background)
    assert chat_env["turns"] == []


def test_envelope_with_calls_and_messages_dispatches_both(
    client, chat_env, background, monkeypatch
) -> None:
    recorded_events: list[str] = []

    class _FakeCalls:
        async def is_duplicate(self, event_id: str) -> bool:
            return False

        async def record_event(self, *, event_id, call_id, event_type, payload):
            recorded_events.append(event_type)
            return {"event_id": event_id}

        async def upsert_call(self, **kwargs):
            return kwargs

    monkeypatch.setattr(webhook_route, "calls", _FakeCalls())

    payload = _envelope(
        _text_message("hi", wamid="wamid.BOTH"),
        extra_value={
            "calls": [{"id": "call-9", "from": SENDER, "event": "terminate",
                       "timestamp": "1752566500"}]
        },
    )
    assert _post(client, payload).status_code == 200
    _drain(background)

    assert recorded_events == ["terminate"]
    assert len(chat_env["turns"]) == 1
