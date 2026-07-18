"""US2: caller speaks and hears a spoken reply (T012, SC-002).

Proves a finished caller utterance yields an audible reply, and an empty agent reply degrades
to a spoken fallback (never silence).
"""

from __future__ import annotations

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


async def test_caller_utterance_is_answered_aloud(monkeypatch: pytest.MonkeyPatch) -> None:
    bridges = patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="")
    patch_tts(monkeypatch)
    patch_agent_events(monkeypatch, text_reply)
    patch_transcripts(monkeypatch, ["what time is checkout"])

    await media_session.start_session("call-r", "+15550003333", "offer")
    await await_session("call-r")

    assert [c.audio for c in bridges["call-r"].played] == [b"reply-what time is checkout"]


async def test_all_text_deltas_are_spoken_as_one_complete_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the call reply must include every agent text event, not just the first."""

    bridges = patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="")
    patch_tts(monkeypatch)
    patch_transcripts(monkeypatch, ["tell me about late checkout"])

    def events(message):
        return [
            AgentStreamEvent(kind="text_delta", text="Late checkout "),
            AgentStreamEvent(kind="text_delta", text="is available "),
            AgentStreamEvent(kind="text_delta", text="until 1 PM."),
        ]

    patch_agent_events(monkeypatch, events)

    await media_session.start_session("call-deltas", "+15550005550", "offer")
    await await_session("call-deltas")

    assert [c.audio for c in bridges["call-deltas"].played] == [
        b"Late checkout is available until 1 PM."
    ]


async def test_empty_reply_degrades_to_spoken_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-002: when the agent produces no text, the caller hears the fallback, not silence."""

    bridges = patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="")
    patch_tts(monkeypatch)
    # Agent yields no events at all → empty reply.
    patch_agent_events(monkeypatch, lambda message: [])
    patch_transcripts(monkeypatch, ["hello?"])

    await media_session.start_session("call-e", "+15550004444", "offer")
    await await_session("call-e")

    assert [c.audio for c in bridges["call-e"].played] == [media_session._AGENT_FALLBACK.encode()]
