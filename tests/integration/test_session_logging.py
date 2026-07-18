"""US4: full call flow visible in backend logs (T019, SC-005 / SC-007).

Proves a lookup turn produces the complete ordered log timeline tied to one call_id, and that
two concurrent calls produce separable, correctly-attributed records.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.services.media import session as media_session
from app.services.media.types import AgentStreamEvent
from tests.integration.media_fakes import (
    await_session,
    patch_agent_events,
    patch_bridge,
    patch_settings,
    patch_transcripts,
    patch_tts,
    text_reply,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    media_session._sessions.clear()
    yield
    media_session._sessions.clear()


def _events_for(caplog: pytest.LogCaptureFixture, call_id: str) -> list[str]:
    return [
        rec.event  # type: ignore[attr-defined]
        for rec in caplog.records
        if getattr(rec, "call_id", None) == call_id and hasattr(rec, "event")
    ]


async def test_full_timeline_for_a_lookup_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """SC-005: accept → welcome → turn0 → tool_call/filler/result → playback → turn → ended."""

    patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="Hi there")
    patch_tts(monkeypatch)
    patch_transcripts(monkeypatch, ["book a room"])

    def events(message):
        return [
            AgentStreamEvent(kind="tool_call", tool_name="book_room"),
            AgentStreamEvent(kind="tool_output", tool_name="book_room", ok=True),
            AgentStreamEvent(kind="text_delta", text="Booked."),
        ]

    patch_agent_events(monkeypatch, events)

    with caplog.at_level(logging.INFO, logger="app.call"):
        await media_session.start_session("call-log", "+15550008888", "offer")
        await await_session("call-log")

    seq = _events_for(caplog, "call-log")
    # The full milestone timeline, in order, all tied to one call_id.
    assert seq == [
        "call_attended",
        "call_welcome",
        "call_turn",  # turn 0 = welcome
        "call_transcript",  # what STT heard from the caller (final)
        "call_tool_call",
        "call_filler",
        "call_tool_result",
        "call_playback",  # start
        "call_playback",  # stop
        "call_turn",  # turn 1 = the lookup reply
        "call_ended",
    ]


async def test_concurrent_calls_stay_separable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """SC-007: interleaved concurrent-call records are each attributed to the correct call."""

    patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="")
    patch_tts(monkeypatch)
    patch_agent_events(monkeypatch, text_reply)
    patch_transcripts(monkeypatch, ["hello"])

    with caplog.at_level(logging.INFO, logger="app.call"):
        await asyncio.gather(
            media_session.start_session("call-a", "+15550000001", "offer-a"),
            media_session.start_session("call-b", "+15550000002", "offer-b"),
        )
        await asyncio.gather(await_session("call-a"), await_session("call-b"))

    a_events = _events_for(caplog, "call-a")
    b_events = _events_for(caplog, "call-b")
    # Each call has its own complete bookend records; neither is empty nor cross-attributed.
    assert a_events[0] == "call_attended" and a_events[-1] == "call_ended"
    assert b_events[0] == "call_attended" and b_events[-1] == "call_ended"
    # No record is missing a call_id (every flow record is correlated).
    flow = {
        "call_attended", "call_turn", "call_playback", "call_ended",
    }
    for rec in caplog.records:
        if getattr(rec, "event", None) in flow:
            assert getattr(rec, "call_id", None) in {"call-a", "call-b"}
