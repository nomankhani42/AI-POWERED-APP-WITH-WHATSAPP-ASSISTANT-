"""US1: reliable greeting then hand-off (T009, FR-020 / SC-001).

Proves the welcome plays to completion before any listening begins and that caller audio
arriving during the welcome does not interrupt it or start a turn (welcome is non-interruptible).
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.media import session as media_session
from app.services.media.types import TranscriptSegment
from tests.integration.media_fakes import (
    await_session,
    patch_agent_events,
    patch_bridge,
    patch_settings,
    patch_tts,
    text_reply,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    media_session._sessions.clear()
    yield
    media_session._sessions.clear()


async def test_welcome_plays_fully_then_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-001: the full welcome is the first audio, before any reply."""

    bridges = patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="Welcome to the hotel!", timeout=30.0)
    patch_tts(monkeypatch)
    patch_agent_events(monkeypatch, text_reply)

    async def transcripts(audio_chunks, *, call_id, sample_rate=None):
        yield TranscriptSegment(call_id=call_id, text="hi", is_final=True, ts=0.0)

    monkeypatch.setattr(media_session, "transcribe_stream", transcripts)

    await media_session.start_session("call-w", "+15550001111", "offer")
    await await_session("call-w")

    played = [c.audio for c in bridges["call-w"].played]
    assert played[0] == b"Welcome to the hotel!"  # greeting is first
    assert played[1] == b"reply-hi"  # the caller's turn is handled after the greeting


async def test_welcome_is_not_interrupted_by_caller_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-020: barge-in does not apply to the welcome; the greeting completes in full.

    Even with barge-in enabled and a caller segment available immediately, the welcome (spoken
    on the non-barge-in path, before the listen loop starts) is never stopped.
    """

    bridges = patch_bridge(monkeypatch)
    patch_settings(monkeypatch, welcome="Long welcome message", timeout=0.05, barge_in=True)
    patch_tts(monkeypatch)
    patch_agent_events(monkeypatch, text_reply)

    async def talk_immediately(audio_chunks, *, call_id, sample_rate=None):
        # Caller "talks" the instant listening could start.
        yield TranscriptSegment(call_id=call_id, text="interrupt", is_final=True, ts=0.0)

    monkeypatch.setattr(media_session, "transcribe_stream", talk_immediately)

    async def fake_terminate(call_id):
        return {}

    monkeypatch.setattr(media_session.meta_calling, "terminate", fake_terminate)

    await media_session.start_session("call-x", "+15550002222", "offer")
    await await_session("call-x")

    played = [c.audio for c in bridges["call-x"].played]
    # The welcome played in full and was never stopped by barge-in during the greeting.
    assert played[0] == b"Long welcome message"
    assert bridges["call-x"].stopped is False
