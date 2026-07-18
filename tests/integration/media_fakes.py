"""Shared fakes for the feature-005 media session integration tests.

Not a test module (no ``test_`` prefix) — imported by test_session_*.py. Mirrors the mocking
style already used by test_call_session.py / test_call_pipeline.py: the media bridge, STT, TTS,
and the agent turn are all stubbed at their module boundary so no network / audio / keys are
needed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from app.services.media import session as media_session
from app.services.media.types import AgentStreamEvent, SpeechChunk, TranscriptSegment


class FakeBridge:
    """Stands in for ``MediaBridge``: records played audio, tracks stop/close."""

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.played: list[SpeechChunk] = []
        self.stopped = False
        self.closed = False

    async def answer(self, offer_sdp: str) -> str:
        return f"answer-sdp-for-{self.call_id}"

    def inbound_pcm(self) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            yield b"\x00\x00"

        return _gen()

    async def play(self, chunks: AsyncIterator[SpeechChunk]) -> None:
        async for chunk in chunks:
            self.played.append(chunk)

    def stop_playback(self) -> None:
        self.stopped = True

    async def close(self) -> None:
        self.closed = True


def patch_bridge(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeBridge]:
    bridges: dict[str, FakeBridge] = {}

    def factory(call_id: str) -> FakeBridge:
        bridge = FakeBridge(call_id)
        bridges[call_id] = bridge
        return bridge

    monkeypatch.setattr(media_session, "MediaBridge", factory)
    return bridges


def patch_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    welcome: str = "",
    timeout: float = 30.0,
    barge_in: bool = False,
) -> None:
    monkeypatch.setattr(
        media_session,
        "get_settings",
        lambda: SimpleNamespace(
            welcome_message=welcome,
            caller_silence_timeout_s=timeout,
            barge_in_enabled=barge_in,
            provider_retry_attempts=1,
        ),
    )

    async def remember_noop(conversation_id: str, text: str) -> None:
        return None

    monkeypatch.setattr(media_session, "remember_assistant_message", remember_noop)


def patch_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTtsSession:
        def __init__(self, call_id: str) -> None:
            self.call_id = call_id

        async def synthesize_stream(self, text_chunks):
            text = text_chunks if isinstance(text_chunks, str) else "".join(
                [piece async for piece in text_chunks]
            )
            if text:
                yield SpeechChunk(call_id=self.call_id, audio=text.encode(), sample_rate=48000)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(media_session, "DeepgramTtsSession", FakeTtsSession)


def patch_agent_events(monkeypatch: pytest.MonkeyPatch, per_message):
    """Patch run_turn_events with ``per_message(message) -> list[AgentStreamEvent]``."""

    async def fake_run_turn_events(message, phone_number, conversation_id=None, channel="voice"):
        for event in per_message(message):
            yield event

    monkeypatch.setattr(media_session, "run_turn_events", fake_run_turn_events)


def text_reply(message: str) -> list[AgentStreamEvent]:
    """A plain no-tool reply: one text delta."""

    return [AgentStreamEvent(kind="text_delta", text=f"reply-{message}")]


def patch_transcripts(monkeypatch: pytest.MonkeyPatch, utterances: list[str]) -> None:
    async def fake_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        for text in utterances:
            yield TranscriptSegment(call_id=call_id, text=text, is_final=True, ts=0.0)

    monkeypatch.setattr(media_session, "transcribe_stream", fake_transcribe_stream)


async def await_session(call_id: str, timeout: float = 2.0) -> None:
    session = media_session._sessions.get(call_id)
    if session is not None and session._task is not None:
        await asyncio.wait_for(session._task, timeout=timeout)
