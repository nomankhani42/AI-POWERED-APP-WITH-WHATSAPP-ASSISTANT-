"""US3: spoken tool-tailored filler while a tool runs (T016, SC-003 / SC-004).

Proves a tool call makes the session speak the tailored filler *before* the reply, a no-tool
turn speaks no filler, and a turn with several tools speaks one tailored filler per tool.
"""

from __future__ import annotations

import pytest

from app.services.media import session as media_session
from app.services.media.fillers import filler_for
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


async def test_tool_turn_speaks_filler_before_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-003: the tailored filler is spoken before the answer on a tool turn."""

    bridges = patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="")
    patch_tts(monkeypatch)
    patch_transcripts(monkeypatch, ["what rooms are free"])

    def events(message):
        return [
            AgentStreamEvent(kind="tool_call", tool_name="check_availability"),
            AgentStreamEvent(kind="tool_output", tool_name="check_availability", ok=True),
            AgentStreamEvent(kind="text_delta", text="Room 5 is available."),
        ]

    patch_agent_events(monkeypatch, events)

    await media_session.start_session("call-t", "+15550005555", "offer")
    await await_session("call-t")

    played = [c.audio for c in bridges["call-t"].played]
    # Filler for the specific tool comes first, then the actual answer.
    assert played == [filler_for("check_availability").encode(), b"Room 5 is available."]


async def test_no_tool_turn_speaks_no_filler(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-004: a turn answered without a tool inserts no filler."""

    bridges = patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="")
    patch_tts(monkeypatch)
    patch_agent_events(monkeypatch, text_reply)
    patch_transcripts(monkeypatch, ["what can you do"])

    await media_session.start_session("call-n", "+15550006666", "offer")
    await await_session("call-n")

    played = [c.audio for c in bridges["call-n"].played]
    assert played == [b"reply-what can you do"]  # exactly the reply, no filler prepended


async def test_multiple_tools_speak_one_filler_each(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge case: chained tools narrate one tailored filler per tool, then the answer."""

    bridges = patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="")
    patch_tts(monkeypatch)
    patch_transcripts(monkeypatch, ["book the deluxe next weekend"])

    def events(message):
        return [
            AgentStreamEvent(kind="tool_call", tool_name="check_availability"),
            AgentStreamEvent(kind="tool_output", tool_name="check_availability", ok=True),
            AgentStreamEvent(kind="tool_call", tool_name="book_room"),
            AgentStreamEvent(kind="tool_output", tool_name="book_room", ok=True),
            AgentStreamEvent(kind="text_delta", text="Booked! Ref ABC123."),
        ]

    patch_agent_events(monkeypatch, events)

    await media_session.start_session("call-m", "+15550007777", "offer")
    await await_session("call-m")

    played = [c.audio for c in bridges["call-m"].played]
    assert played == [
        filler_for("check_availability").encode(),
        filler_for("book_room").encode(),
        b"Booked! Ref ABC123.",
    ]
