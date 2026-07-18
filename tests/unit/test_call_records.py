"""Unit tests for the monotonic Call state-transition logic (T006).

Covers ``app.db.calls.apply_transition`` (pure, no DB) and ``upsert_call`` (DB calls mocked
at the Beanie ``Call`` class boundary — no live Mongo needed).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.db import calls as calls_db
from app.db.documents import Call, CallStatus


@pytest.fixture(autouse=True)
def _stub_call_document_settings(monkeypatch):
    """Let ``Call(...)`` construct in-memory without a live Mongo connection.

    Beanie's ``Document.__init__`` calls ``get_pymongo_collection()``, which normally
    requires ``init_beanie`` to have run against a real database. These are pure/mocked-
    persistence tests, so a minimal stub settings object (only the attribute Beanie reads at
    construction time) is enough — no DB, network, or real Beanie init needed.
    """

    monkeypatch.setattr(
        Call, "_document_settings", SimpleNamespace(pymongo_collection=None), raising=False
    )


def _new_call(status: CallStatus = CallStatus.ringing) -> Call:
    return Call(
        call_id="c1",
        wa_call_from="15557654321",
        display_phone_number="15550001111",
        conversation_id="15557654321",
        status=status,
    )


# --- apply_transition: pure logic, no persistence involved ---


def test_ringing_advances_to_connecting():
    call = _new_call(CallStatus.ringing)
    calls_db.apply_transition(call, status=CallStatus.connecting)
    assert call.status == CallStatus.connecting


def test_connected_sets_connected_at_once():
    call = _new_call(CallStatus.connecting)
    calls_db.apply_transition(call, status=CallStatus.connected)
    assert call.status == CallStatus.connected
    first_connected_at = call.connected_at
    assert first_connected_at is not None

    # Re-applying "connected" must not bump the timestamp again.
    calls_db.apply_transition(call, status=CallStatus.connected)
    assert call.connected_at == first_connected_at


def test_ended_sets_ended_at_and_reason():
    call = _new_call(CallStatus.connected)
    calls_db.apply_transition(call, status=CallStatus.ended, end_reason="caller_hangup")
    assert call.status == CallStatus.ended
    assert call.ended_at is not None
    assert call.end_reason == "caller_hangup"


def test_ended_call_never_regresses_to_connected():
    call = _new_call(CallStatus.ended)
    call.ended_at = datetime.now(tz=timezone.utc)
    calls_db.apply_transition(call, status=CallStatus.connected)
    assert call.status == CallStatus.ended
    # connected_at must not be back-filled once the call is terminal.
    assert call.connected_at is None


def test_failed_call_never_regresses():
    call = _new_call(CallStatus.failed)
    calls_db.apply_transition(call, status=CallStatus.ringing)
    assert call.status == CallStatus.failed


def test_none_status_is_a_noop():
    call = _new_call(CallStatus.connecting)
    calls_db.apply_transition(call, status=None)
    assert call.status == CallStatus.connecting


# --- upsert_call: Call.find_one/insert/save mocked, no live Mongo ---


async def test_upsert_call_creates_new_call(monkeypatch):
    monkeypatch.setattr(Call, "find_one", AsyncMock(return_value=None))
    inserted = AsyncMock()
    monkeypatch.setattr(Call, "insert", inserted)

    call = await calls_db.upsert_call(
        call_id="c-new",
        wa_call_from="15551112222",
        display_phone_number="15550001111",
        status=CallStatus.connecting,
    )

    assert call.call_id == "c-new"
    assert call.status == CallStatus.connecting
    assert call.conversation_id == "15551112222"
    inserted.assert_awaited_once()


async def test_upsert_call_terminate_before_connect_reconciles_to_ended(monkeypatch):
    """Out-of-order terminate for a call never seen before creates it already ended."""

    monkeypatch.setattr(Call, "find_one", AsyncMock(return_value=None))
    monkeypatch.setattr(Call, "insert", AsyncMock())

    call = await calls_db.upsert_call(
        call_id="c-orphan",
        wa_call_from="15551112222",
        display_phone_number="15550001111",
        status=CallStatus.ended,
        end_reason="caller_hangup",
    )

    assert call.status == CallStatus.ended
    assert call.connected_at is None
    assert call.ended_at is not None
    assert call.end_reason == "caller_hangup"


async def test_upsert_call_never_regresses_an_ended_call(monkeypatch):
    existing = _new_call(CallStatus.ended)
    existing.ended_at = datetime.now(tz=timezone.utc)
    monkeypatch.setattr(Call, "find_one", AsyncMock(return_value=existing))
    saved = AsyncMock()
    monkeypatch.setattr(Call, "save", saved)

    call = await calls_db.upsert_call(call_id="c1", status=CallStatus.connected)

    assert call.status == CallStatus.ended
    saved.assert_awaited_once()
