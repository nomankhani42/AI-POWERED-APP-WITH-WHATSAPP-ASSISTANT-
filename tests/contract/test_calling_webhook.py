"""Contract tests for the WhatsApp calling webhook (T005).

See contracts/whatsapp-calling-webhook.md. Mongo/Beanie and Redis are never touched — the
route's ``calls`` module dependency is patched onto an in-memory fake so these tests need no
live database, Redis, or network. The signature is computed in-test from the same dummy
``WHATSAPP_APP_SECRET`` set in ``tests/conftest.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json

import pytest

import app.api.routes.whatsapp_calling as webhook_route
from app.core.config import get_settings
from app.db.documents import CallStatus


class _FakeCalls:
    """In-memory stand-in for ``app.db.calls`` — tracks dedupe, events, and call state."""

    def __init__(self) -> None:
        self.seen_event_ids: set[str] = set()
        self.events: list[dict] = []
        self.calls_by_id: dict[str, dict] = {}

    async def is_duplicate(self, event_id: str) -> bool:
        if event_id in self.seen_event_ids:
            return True
        self.seen_event_ids.add(event_id)
        return False

    async def record_event(self, *, event_id, call_id, event_type, payload):
        record = {
            "event_id": event_id,
            "call_id": call_id,
            "event_type": event_type,
            "payload": payload,
        }
        self.events.append(record)
        return record

    async def upsert_call(
        self,
        *,
        call_id,
        status=None,
        wa_call_from=None,
        display_phone_number=None,
        conversation_id=None,
        end_reason=None,
    ):
        call = self.calls_by_id.setdefault(
            call_id, {"call_id": call_id, "status": CallStatus.ringing}
        )
        if wa_call_from:
            call["wa_call_from"] = wa_call_from
        if display_phone_number:
            call["display_phone_number"] = display_phone_number
        if status is not None and call["status"] not in (CallStatus.ended, CallStatus.failed):
            call["status"] = status
            if status in (CallStatus.ended, CallStatus.failed):
                call["end_reason"] = end_reason
        return call


@pytest.fixture
def fake_calls(monkeypatch) -> _FakeCalls:
    fake = _FakeCalls()
    monkeypatch.setattr(webhook_route, "calls", fake)
    return fake


@pytest.fixture(autouse=True)
def isolate_background_media(monkeypatch) -> None:
    """Keep acknowledgement tests from opening real WebRTC/Graph connections."""

    def discard(coro: object) -> None:
        if inspect.iscoroutine(coro):
            coro.close()

    monkeypatch.setattr(webhook_route, "_fire_and_forget", discard)


def _sign(body: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _connect_payload(call_id: str = "call-1", timestamp: str = "1720180800") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "calls",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001111",
                                "phone_number_id": "phone-1",
                            },
                            "calls": [
                                {
                                    "id": call_id,
                                    "from": "15557654321",
                                    "event": "connect",
                                    "timestamp": timestamp,
                                    "session": {"sdp_type": "offer", "sdp": "v=0..."},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _terminate_payload(call_id: str = "call-1", timestamp: str = "1720180900") -> dict:
    payload = _connect_payload(call_id=call_id, timestamp=timestamp)
    call_event = payload["entry"][0]["changes"][0]["value"]["calls"][0]
    call_event["event"] = "terminate"
    call_event.pop("session", None)
    return payload


def _post(client, payload: dict, *, secret: str | None = None, bad_signature: bool = False):
    body = json.dumps(payload).encode("utf-8")
    if bad_signature:
        signature = "sha256=" + "0" * 64
    else:
        signature = _sign(body, secret or get_settings().whatsapp_app_secret)
    return client.post(
        "/whatsapp/webhook",
        content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )


# --- GET verification handshake ---


def test_verify_handshake_success(client) -> None:
    settings = get_settings()
    resp = client.get(
        "/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.whatsapp_verify_token,
            "hub.challenge": "1234567",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "1234567"


def test_verify_handshake_token_mismatch(client) -> None:
    resp = client.get(
        "/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "1234567",
        },
    )
    assert resp.status_code == 403


# --- POST call lifecycle events ---


def test_valid_connect_event_creates_call_and_event(client, fake_calls) -> None:
    resp = _post(client, _connect_payload(call_id="call-connect"))

    assert resp.status_code == 200
    assert resp.json() == {"status": "received"}
    assert len(fake_calls.events) == 1
    assert fake_calls.calls_by_id["call-connect"]["status"] == CallStatus.connecting


def test_bad_signature_returns_200_but_records_nothing(client, fake_calls) -> None:
    resp = _post(client, _connect_payload(call_id="call-bad"), bad_signature=True)

    assert resp.status_code == 200
    assert resp.json() == {"status": "received"}
    assert fake_calls.events == []
    assert "call-bad" not in fake_calls.calls_by_id


def test_duplicate_event_is_acknowledged_without_a_second_record(client, fake_calls) -> None:
    payload = _connect_payload(call_id="call-dup")

    first = _post(client, payload)
    second = _post(client, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(fake_calls.events) == 1


def test_terminate_event_ends_the_call(client, fake_calls) -> None:
    _post(client, _connect_payload(call_id="call-term"))
    resp = _post(client, _terminate_payload(call_id="call-term"))

    assert resp.status_code == 200
    assert resp.json() == {"status": "received"}
    assert fake_calls.calls_by_id["call-term"]["status"] == CallStatus.ended


@pytest.mark.asyncio
async def test_media_starts_only_after_accept_and_connected_persistence(
    monkeypatch: pytest.MonkeyPatch, fake_calls: _FakeCalls
) -> None:
    events: list[str] = []
    session_exists = False

    class FakeMedia:
        @staticmethod
        def has_session(call_id: str) -> bool:
            return session_exists

        @staticmethod
        async def prepare_session(call_id: str, caller: str, offer: str) -> str:
            nonlocal session_exists
            events.append("prepare")
            session_exists = True
            return "answer-sdp"

        @staticmethod
        async def wait_session_ready(call_id: str) -> bool:
            events.append("ready")
            return True

        @staticmethod
        def activate_session(call_id: str) -> bool:
            events.append("activate")
            return True

        @staticmethod
        async def stop_session(call_id: str) -> None:
            events.append("stop")

    async def pre_accept(call_id: str, answer: str) -> dict:
        events.append("pre_accept")
        return {}

    async def accept(call_id: str, answer: str) -> dict:
        events.append("accept")
        return {}

    original_upsert = fake_calls.upsert_call

    async def upsert(**kwargs):
        events.append(f"persist:{kwargs.get('status').value}")
        return await original_upsert(**kwargs)

    monkeypatch.setattr(webhook_route, "media_session", FakeMedia)
    monkeypatch.setattr(webhook_route.meta_calling, "pre_accept", pre_accept)
    monkeypatch.setattr(webhook_route.meta_calling, "accept", accept)
    monkeypatch.setattr(fake_calls, "upsert_call", upsert)

    await webhook_route._start_media("call-order", "+1555", "offer-sdp")

    assert events == ["prepare", "pre_accept", "accept", "ready", "persist:connected", "activate"]
    assert fake_calls.calls_by_id["call-order"]["status"] == CallStatus.connected


@pytest.mark.asyncio
async def test_media_accept_failure_stops_session_and_marks_call_failed(
    monkeypatch: pytest.MonkeyPatch, fake_calls: _FakeCalls
) -> None:
    stopped: list[str] = []

    class FakeMedia:
        @staticmethod
        def has_session(call_id: str) -> bool:
            return False

        @staticmethod
        async def prepare_session(call_id: str, caller: str, offer: str) -> str:
            return "answer-sdp"

        @staticmethod
        async def stop_session(call_id: str) -> None:
            stopped.append(call_id)

    async def pre_accept(call_id: str, answer: str) -> dict:
        return {}

    async def reject_accept(call_id: str, answer: str) -> dict:
        raise RuntimeError("accept rejected")

    monkeypatch.setattr(webhook_route, "media_session", FakeMedia)
    monkeypatch.setattr(webhook_route.meta_calling, "pre_accept", pre_accept)
    monkeypatch.setattr(webhook_route.meta_calling, "accept", reject_accept)

    await webhook_route._start_media("call-failed", "+1555", "offer-sdp")

    assert stopped == ["call-failed"]
    assert fake_calls.calls_by_id["call-failed"]["status"] == CallStatus.failed
    assert fake_calls.calls_by_id["call-failed"]["end_reason"] == "media_start_failure"
