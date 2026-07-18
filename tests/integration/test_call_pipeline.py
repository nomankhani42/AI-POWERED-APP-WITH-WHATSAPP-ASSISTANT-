"""Integration test for the live-call media pipeline (T020, Phase 6).

Exercises ``services/media/session.py``'s STT -> ``run_turn`` -> TTS orchestration loop
with the aiortc bridge, Deepgram STT, Cartesia TTS, and the agent turn all mocked at
their module boundaries — no network, no real media, no API keys. Proves (quickstart.md
Scenario 5):

- two concurrent ``call_id``s never cross-talk (FR-013): each call's synthesized reply is
  derived only from its own transcript;
- a provider failure (``SttError``/``TtsError``) tears the session down gracefully
  without hanging (FR-009).

The real Opus<->PCM codec round trip lives in ``services/media/webrtc.py`` and is NOT
exercised here — per that module's docstring, aiortc's own resampler handles Opus
encode/decode, which is impractical to assert without a live RTP peer. That leaves this
test to focus on the orchestration loop with the bridge mocked behind its
``answer``/``inbound_pcm``/``play``/``close`` interface, which is the acceptable,
documented trade-off called out in this task's brief. Real-call validation of the actual
audio round trip is left for a live Meta call against Scenario 5 of quickstart.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from app.services.media import session as media_session
from app.services.media.types import AgentStreamEvent, SpeechChunk, TranscriptSegment
from app.services.stt import SttError
from app.services.tts import TtsError


class _FakeBridge:
    """Stands in for ``MediaBridge``: records what's played, tracks ``close()``."""

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.played: list[SpeechChunk] = []
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


@pytest.fixture(autouse=True)
def _reset_registry():
    media_session._sessions.clear()
    yield
    media_session._sessions.clear()


@pytest.fixture(autouse=True)
def _no_welcome(monkeypatch: pytest.MonkeyPatch):
    """This suite asserts only the reply audio, so disable the US4 auto-welcome here and use a
    generous silence timeout (the auto-welcome + turn loop are covered by test_call_session.py)."""

    monkeypatch.setattr(
        media_session,
        "get_settings",
        lambda: SimpleNamespace(
            welcome_message="",
            caller_silence_timeout_s=30.0,
            barge_in_enabled=False,
            provider_retry_attempts=1,
        ),
    )


def _patch_bridge(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeBridge]:
    """Replace ``MediaBridge`` with a factory producing tracked ``_FakeBridge`` instances."""

    bridges: dict[str, _FakeBridge] = {}

    def factory(call_id: str) -> _FakeBridge:
        bridge = _FakeBridge(call_id)
        bridges[call_id] = bridge
        return bridge

    monkeypatch.setattr(media_session, "MediaBridge", factory)
    return bridges


async def _await_session(call_id: str) -> None:
    """Wait for a started session's background loop task to finish (or time out)."""

    session = media_session._sessions.get(call_id)
    if session is not None and session._task is not None:
        await asyncio.wait_for(session._task, timeout=1)


def _patch_tts_session(monkeypatch: pytest.MonkeyPatch, synthesize_stream) -> None:
    class FakeTtsSession:
        def __init__(self, call_id: str) -> None:
            self.call_id = call_id

        def synthesize_stream(self, text_chunks):
            return synthesize_stream(text_chunks, call_id=self.call_id)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(media_session, "DeepgramTtsSession", FakeTtsSession)


async def test_two_concurrent_calls_do_not_cross_talk(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-013: each call's spoken reply derives only from its own transcript."""

    bridges = _patch_bridge(monkeypatch)
    transcripts = {"call-a": "what is the weather", "call-b": "book me a table"}

    async def fake_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        yield TranscriptSegment(call_id=call_id, text=transcripts[call_id], is_final=True, ts=0.0)

    async def fake_run_turn_events(message, phone_number, conversation_id=None, channel="voice"):
        yield AgentStreamEvent(kind="text_delta", text=f"reply:{message}:{phone_number}")

    async def fake_synthesize_stream(text_chunks, *, call_id):
        text = text_chunks if isinstance(text_chunks, str) else "".join(
            [piece async for piece in text_chunks]
        )
        if text:
            yield SpeechChunk(call_id=call_id, audio=text.encode(), sample_rate=48000)

    monkeypatch.setattr(media_session, "transcribe_stream", fake_transcribe_stream)
    monkeypatch.setattr(media_session, "run_turn_events", fake_run_turn_events)
    _patch_tts_session(monkeypatch, fake_synthesize_stream)

    await asyncio.gather(
        media_session.start_session("call-a", "+10000000001", "offer-a"),
        media_session.start_session("call-b", "+10000000002", "offer-b"),
    )
    await asyncio.gather(_await_session("call-a"), _await_session("call-b"))

    assert [c.audio for c in bridges["call-a"].played] == [
        b"reply:what is the weather:+10000000001"
    ]
    assert [c.audio for c in bridges["call-b"].played] == [b"reply:book me a table:+10000000002"]
    assert bridges["call-a"].closed is True
    assert bridges["call-b"].closed is True
    assert "call-a" not in media_session._sessions
    assert "call-b" not in media_session._sessions


async def test_stt_failure_retries_then_apologizes_and_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """T028 / FR-011: an unrecoverable STT stream is retried once, then apology + graceful end."""

    bridges = _patch_bridge(monkeypatch)

    attempts = 0

    async def failing_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        nonlocal attempts
        attempts += 1
        raise SttError("simulated deepgram outage")
        yield  # pragma: no cover - keeps this an async generator function

    run_turn_called = False

    async def fake_run_turn_events(message, phone_number, conversation_id=None, channel="voice"):
        nonlocal run_turn_called
        run_turn_called = True
        yield AgentStreamEvent(kind="text_delta", text="should not be reached")  # pragma: no cover

    async def fake_synthesize_stream(text_chunks, *, call_id):
        text = text_chunks if isinstance(text_chunks, str) else "".join(
            [piece async for piece in text_chunks]
        )
        if text:
            yield SpeechChunk(call_id=call_id, audio=text.encode(), sample_rate=48000)

    terminated: list[str] = []

    async def fake_terminate(call_id):
        terminated.append(call_id)
        return {}

    monkeypatch.setattr(media_session, "transcribe_stream", failing_transcribe_stream)
    monkeypatch.setattr(media_session, "run_turn_events", fake_run_turn_events)
    _patch_tts_session(monkeypatch, fake_synthesize_stream)
    monkeypatch.setattr(media_session.meta_calling, "terminate", fake_terminate)

    await media_session.start_session("call-c", "+10000000003", "offer-c")
    await _await_session("call-c")

    assert attempts == 2  # original + one silent retry (provider_retry_attempts=1)
    # After the retry is exhausted the caller hears an apology, then the call ends gracefully.
    assert [c.audio for c in bridges["call-c"].played] == [media_session._AGENT_FALLBACK.encode()]
    assert terminated == ["call-c"]
    assert run_turn_called is False
    assert bridges["call-c"].closed is True
    assert "call-c" not in media_session._sessions


async def test_tts_failure_apologizes_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-011: a ``TtsError`` while speaking the reply yields a spoken apology, call continues."""

    bridges = _patch_bridge(monkeypatch)

    async def fake_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        yield TranscriptSegment(call_id=call_id, text="hello", is_final=True, ts=0.0)

    async def fake_run_turn_events(message, phone_number, conversation_id=None, channel="voice"):
        yield AgentStreamEvent(kind="text_delta", text="a reply")

    async def synthesize_reply_fails(text_chunks, *, call_id):
        text = text_chunks if isinstance(text_chunks, str) else "".join(
            [piece async for piece in text_chunks]
        )
        # The streamed reply fails; fixed fallback/apology text still succeeds.
        if text == "a reply":
            raise TtsError("simulated deepgram outage")
            yield  # pragma: no cover - keeps this an async generator function
        yield SpeechChunk(call_id=call_id, audio=text.encode(), sample_rate=48000)

    monkeypatch.setattr(media_session, "transcribe_stream", fake_transcribe_stream)
    monkeypatch.setattr(media_session, "run_turn_events", fake_run_turn_events)
    _patch_tts_session(monkeypatch, synthesize_reply_fails)

    await media_session.start_session("call-d", "+10000000004", "offer-d")
    await _await_session("call-d")

    # The reply synthesis failed, so the caller heard the spoken apology instead of silence.
    assert [c.audio for c in bridges["call-d"].played] == [media_session._AGENT_FALLBACK.encode()]
    assert bridges["call-d"].closed is True
    assert "call-d" not in media_session._sessions
