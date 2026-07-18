"""Integration test for the automatic conversation loop (T025, US4 / Phase 7).

Exercises ``services/media/session.py``'s welcome + turn loop with the media bridge,
Deepgram STT, Cartesia TTS, the agent turn, and Meta call actions all mocked at their
module boundaries — no network, no real media, no API keys. Proves (quickstart.md
Scenario 6):

- an attended call is logged with the caller's number (FR-015);
- the configurable welcome plays automatically as turn 0 before any caller audio (FR-016);
- a multi-turn exchange speaks one reply per finished utterance and logs one turn each
  (FR-017-020, FR-023);
- a silent caller is re-prompted once then the call is terminated gracefully (edge case);
- a hangup tears the session down cleanly (FR-021).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from app.services.media import session as media_session
from app.services.media.types import (
    AgentStreamEvent,
    ConversationTurn,
    SpeechChunk,
    TranscriptSegment,
)


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


def _patch_bridge(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeBridge]:
    bridges: dict[str, _FakeBridge] = {}

    def factory(call_id: str) -> _FakeBridge:
        bridge = _FakeBridge(call_id)
        bridges[call_id] = bridge
        return bridge

    monkeypatch.setattr(media_session, "MediaBridge", factory)
    return bridges


def _patch_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    welcome: str,
    timeout: float,
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


def _capture_logs(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[str, str]], list[ConversationTurn]]:
    attended: list[tuple[str, str]] = []
    turns: list[ConversationTurn] = []
    monkeypatch.setattr(
        media_session, "log_call_attended", lambda call_id, caller: attended.append((call_id, caller))
    )
    monkeypatch.setattr(media_session, "log_turn", lambda turn: turns.append(turn))
    return attended, turns


def _fake_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTtsSession:
        def __init__(self, call_id: str) -> None:
            self.call_id = call_id

        async def synthesize_stream(self, text_chunks):
            # Accepts fixed prompts and streamed reply generators.
            text = text_chunks if isinstance(text_chunks, str) else "".join(
                [piece async for piece in text_chunks]
            )
            if text:
                yield SpeechChunk(call_id=self.call_id, audio=text.encode(), sample_rate=48000)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(media_session, "DeepgramTtsSession", FakeTtsSession)


def _fake_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the streaming agent turn to echo a single text-delta reply per transcript."""

    async def fake_run_turn_events(message, phone_number, conversation_id=None, channel="voice"):
        yield AgentStreamEvent(kind="text_delta", text=f"reply-{message}")

    monkeypatch.setattr(media_session, "run_turn_events", fake_run_turn_events)


async def _await_session(call_id: str) -> None:
    session = media_session._sessions.get(call_id)
    if session is not None and session._task is not None:
        await asyncio.wait_for(session._task, timeout=2)


async def test_welcome_drains_caller_audio_before_listening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Speech over the protected greeting is consumed instead of buffered for STT."""

    drain_started = asyncio.Event()
    drain_cancelled = asyncio.Event()

    class GreetingBridge:
        def inbound_pcm(self) -> AsyncIterator[bytes]:
            async def stream() -> AsyncIterator[bytes]:
                drain_started.set()
                try:
                    yield b"caller-spoke-over-welcome"
                    await asyncio.Event().wait()
                finally:
                    drain_cancelled.set()

            return stream()

        async def play(self, chunks: AsyncIterator[SpeechChunk]) -> None:
            assert drain_started.is_set()
            async for _ in chunks:
                pass

        async def close(self) -> None:
            return None

    _patch_settings(monkeypatch, welcome="Welcome!", timeout=30.0)
    _fake_tts(monkeypatch)
    monkeypatch.setattr(media_session, "log_welcome", lambda _call_id: None)
    monkeypatch.setattr(media_session, "log_turn", lambda _turn: None)

    session = media_session._CallSession(
        "call-welcome-drain", "+15550000000", GreetingBridge()
    )

    await session._play_welcome()

    assert drain_started.is_set()
    assert drain_cancelled.is_set()


async def test_attended_log_welcome_and_multiturn(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-015/016/019/023: attended log, welcome turn 0, then one reply+turn per utterance."""

    bridges = _patch_bridge(monkeypatch)
    _patch_settings(monkeypatch, welcome="Welcome!", timeout=30.0)
    attended, turns = _capture_logs(monkeypatch)
    _fake_tts(monkeypatch)

    _fake_agent(monkeypatch)

    utterances = [f"msg{i}" for i in range(1, 6)]  # 5 turns (SC-006)

    async def fake_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        for text in utterances:
            yield TranscriptSegment(call_id=call_id, text=text, is_final=True, ts=0.0)

    monkeypatch.setattr(media_session, "transcribe_stream", fake_transcribe_stream)

    await media_session.start_session("call-a", "+15557654321", "offer-a")
    await _await_session("call-a")

    # Attended log with the caller's number (FR-015).
    assert attended == [("call-a", "+15557654321")]

    played = [c.audio for c in bridges["call-a"].played]
    # Welcome plays first (turn 0), then one reply per utterance in order (FR-016/019).
    assert played[0] == b"Welcome!"
    assert played[1:] == [f"reply-{m}".encode() for m in utterances]

    # One logged turn per exchange: turn 0 = welcome, turns 1..5 = transcript/reply (FR-023).
    assert [(t.turn, t.transcript, t.reply) for t in turns] == [
        (0, "", "Welcome!"),
        *[(i, m, f"reply-{m}") for i, m in enumerate(utterances, start=1)],
    ]
    assert bridges["call-a"].closed is True
    assert "call-a" not in media_session._sessions


async def test_low_confidence_transcript_is_clarified_not_sent_to_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridges = _patch_bridge(monkeypatch)
    _patch_settings(monkeypatch, welcome="", timeout=30.0)
    _capture_logs(monkeypatch)
    _fake_tts(monkeypatch)
    agent_messages: list[str] = []

    async def fake_agent(message, phone_number, conversation_id=None, channel="voice"):
        agent_messages.append(message)
        yield AgentStreamEvent(kind="text_delta", text="should not run")

    async def uncertain_transcript(audio_chunks, *, call_id, sample_rate=None):
        yield TranscriptSegment(
            call_id=call_id,
            text="speak",
            is_final=True,
            ts=0.0,
            confidence=0.42,
        )

    monkeypatch.setattr(media_session, "run_turn_events", fake_agent)
    monkeypatch.setattr(media_session, "transcribe_stream", uncertain_transcript)

    await media_session.start_session("call-uncertain", "+15550000000", "offer")
    await _await_session("call-uncertain")

    assert agent_messages == []
    assert [chunk.audio for chunk in bridges["call-uncertain"].played] == [
        media_session._STT_CLARIFICATION.encode()
    ]


async def test_reply_playback_starts_before_agent_stream_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the first reply sentence reaches playback while later deltas generate."""

    bridges = _patch_bridge(monkeypatch)
    _patch_settings(monkeypatch, welcome="", timeout=30.0)
    _capture_logs(monkeypatch)
    first_audio = asyncio.Event()

    class StreamingTtsSession:
        def __init__(self, call_id: str) -> None:
            self.call_id = call_id

        async def synthesize_stream(self, text_chunks):
            pieces = [text_chunks] if isinstance(text_chunks, str) else text_chunks
            if isinstance(pieces, list):
                iterator = pieces
                for piece in iterator:
                    yield SpeechChunk(
                        call_id=self.call_id, audio=piece.encode(), sample_rate=48000
                    )
                return
            async for piece in pieces:
                yield SpeechChunk(call_id=self.call_id, audio=piece.encode(), sample_rate=48000)
                first_audio.set()

        async def close(self) -> None:
            return None

    async def fake_run_turn_events(message, phone_number, conversation_id=None, channel="voice"):
        yield AgentStreamEvent(kind="text_delta", text="First sentence. ")
        await asyncio.wait_for(first_audio.wait(), timeout=1)
        yield AgentStreamEvent(kind="text_delta", text="Second sentence.")

    async def fake_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        yield TranscriptSegment(call_id=call_id, text="hello", is_final=True, ts=0.0)

    monkeypatch.setattr(media_session, "DeepgramTtsSession", StreamingTtsSession)
    monkeypatch.setattr(media_session, "run_turn_events", fake_run_turn_events)
    monkeypatch.setattr(media_session, "transcribe_stream", fake_transcribe_stream)

    await media_session.start_session("call-stream", "+15557654321", "offer")
    await _await_session("call-stream")

    assert [chunk.audio for chunk in bridges["call-stream"].played] == [
        b"First sentence. ",
        b"Second sentence.",
    ]


async def test_welcome_is_remembered_for_call_agent_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: direct welcome audio is seeded into Redis-backed agent memory."""

    _patch_bridge(monkeypatch)
    _patch_settings(monkeypatch, welcome="Welcome!", timeout=30.0)
    _capture_logs(monkeypatch)
    _fake_tts(monkeypatch)

    remembered: list[tuple[str, str]] = []

    async def remember(conversation_id: str, text: str) -> None:
        remembered.append((conversation_id, text))

    agent_calls: list[tuple[str, str, str | None]] = []

    async def fake_run_turn_events(message, phone_number, conversation_id=None, channel="voice"):
        agent_calls.append((message, phone_number, conversation_id))
        yield AgentStreamEvent(kind="text_delta", text="How can I help?")

    async def fake_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        yield TranscriptSegment(call_id=call_id, text="yes", is_final=True, ts=0.0)

    monkeypatch.setattr(media_session, "remember_assistant_message", remember)
    monkeypatch.setattr(media_session, "run_turn_events", fake_run_turn_events)
    monkeypatch.setattr(media_session, "transcribe_stream", fake_transcribe_stream)

    await media_session.start_session("call-memory", "+15557654321", "offer")
    await _await_session("call-memory")

    assert remembered == [("call-memory", "Welcome!")]
    assert agent_calls == [("yes", "+15557654321", "call-memory")]


async def test_silence_reprompt_is_remembered_before_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a later "yes" is grounded in the re-prompt the caller heard."""

    _patch_bridge(monkeypatch)
    _patch_settings(monkeypatch, welcome="", timeout=0.01)
    _capture_logs(monkeypatch)
    _fake_tts(monkeypatch)
    _fake_agent(monkeypatch)

    remembered: list[tuple[str, str]] = []

    async def remember(conversation_id: str, text: str) -> None:
        remembered.append((conversation_id, text))

    async def delayed_transcript(audio_chunks, *, call_id, sample_rate=None):
        await asyncio.sleep(0.03)
        yield TranscriptSegment(call_id=call_id, text="yes", is_final=True, ts=0.0)

    monkeypatch.setattr(media_session, "remember_assistant_message", remember)
    monkeypatch.setattr(media_session, "transcribe_stream", delayed_transcript)

    await media_session.start_session("call-reprompt", "+15550000000", "offer")
    await _await_session("call-reprompt")

    assert remembered == [("call-reprompt", media_session._SILENCE_REPROMPT)]


async def test_silent_caller_reprompts_then_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edge case: after a re-prompt the still-silent call is terminated gracefully."""

    bridges = _patch_bridge(monkeypatch)
    _patch_settings(monkeypatch, welcome="", timeout=0.03)
    _capture_logs(monkeypatch)
    _fake_tts(monkeypatch)

    async def silent_stream(audio_chunks, *, call_id, sample_rate=None):
        await asyncio.sleep(5)  # caller never speaks; cancelled at teardown
        return
        yield  # pragma: no cover - keeps this an async generator

    terminated: list[str] = []

    async def fake_terminate(call_id):
        terminated.append(call_id)
        return {}

    monkeypatch.setattr(media_session, "transcribe_stream", silent_stream)
    monkeypatch.setattr(media_session.meta_calling, "terminate", fake_terminate)

    await media_session.start_session("call-b", "+15550000000", "offer-b")
    await _await_session("call-b")

    # Re-prompted exactly once, then the call was terminated.
    assert [c.audio for c in bridges["call-b"].played] == [media_session._SILENCE_REPROMPT.encode()]
    assert terminated == ["call-b"]
    assert bridges["call-b"].closed is True
    assert "call-b" not in media_session._sessions


async def test_stop_session_tears_down_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-021: a hangup cancels the loop, closes the bridge, and drops the session."""

    bridges = _patch_bridge(monkeypatch)
    _patch_settings(monkeypatch, welcome="", timeout=30.0)
    _capture_logs(monkeypatch)
    _fake_tts(monkeypatch)

    async def silent_stream(audio_chunks, *, call_id, sample_rate=None):
        await asyncio.sleep(5)
        return
        yield  # pragma: no cover

    async def fake_run_turn_events(message, phone_number, conversation_id=None, channel="voice"):  # pragma: no cover
        return
        yield

    monkeypatch.setattr(media_session, "transcribe_stream", silent_stream)
    monkeypatch.setattr(media_session, "run_turn_events", fake_run_turn_events)

    await media_session.start_session("call-c", "+15551111111", "offer-c")
    await media_session.stop_session("call-c")

    assert bridges["call-c"].closed is True
    assert "call-c" not in media_session._sessions


async def test_barge_in_stops_playback_when_caller_speaks(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-013/SC-008: a caller segment during a reply stops playback and switches to listening."""

    class _BlockingBridge:
        """Bridge whose ``play`` keeps 'playing' (awaits release) so barge-in can interrupt it."""

        def __init__(self, call_id: str) -> None:
            self.call_id = call_id
            self.played: list[SpeechChunk] = []
            self.stopped = False
            self.closed = False
            self._release = asyncio.Event()

        async def answer(self, offer_sdp: str) -> str:
            return "answer"

        def inbound_pcm(self):
            async def _gen():
                yield b"\x00\x00"

            return _gen()

        async def play(self, chunks: AsyncIterator[SpeechChunk]) -> None:
            async for chunk in chunks:
                self.played.append(chunk)
            await self._release.wait()  # audio is still "playing" until released/cancelled

        def stop_playback(self) -> None:
            self.stopped = True
            self._release.set()

        async def close(self) -> None:
            self.closed = True

    bridges: dict[str, _BlockingBridge] = {}

    def factory(call_id: str) -> _BlockingBridge:
        bridge = _BlockingBridge(call_id)
        bridges[call_id] = bridge
        return bridge

    monkeypatch.setattr(media_session, "MediaBridge", factory)
    _patch_settings(monkeypatch, welcome="", timeout=30.0, barge_in=True)
    _capture_logs(monkeypatch)
    _fake_tts(monkeypatch)
    _fake_agent(monkeypatch)

    async def fake_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        yield TranscriptSegment(call_id=call_id, text="hello", is_final=True, ts=0.0)  # turn 1
        yield TranscriptSegment(call_id=call_id, text="wait", is_final=False, ts=0.0)  # barge-in

    monkeypatch.setattr(media_session, "transcribe_stream", fake_transcribe_stream)

    await media_session.start_session("call-d", "+15559990000", "offer-d")
    await _await_session("call-d")

    # The caller's interim segment during the reply stopped playback (barge-in).
    assert bridges["call-d"].stopped is True
    # The first reply's audio was produced before the interruption.
    assert [c.audio for c in bridges["call-d"].played] == [b"reply-hello"]
    assert bridges["call-d"].closed is True


async def test_playback_ignores_stt_stream_end_during_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: STT stream end/no-speech events must not cut off GPT playback."""

    class _SlowBridge:
        def __init__(self, call_id: str) -> None:
            self.call_id = call_id
            self.played: list[SpeechChunk] = []
            self.stopped = False
            self.closed = False

        async def answer(self, offer_sdp: str) -> str:
            return "answer"

        def inbound_pcm(self):
            async def _gen():
                yield b"\x00\x00"

            return _gen()

        async def play(self, chunks: AsyncIterator[SpeechChunk]) -> None:
            async for chunk in chunks:
                self.played.append(chunk)
            await asyncio.sleep(0.05)

        def stop_playback(self) -> None:
            self.stopped = True

        async def close(self) -> None:
            self.closed = True

    bridges: dict[str, _SlowBridge] = {}

    def factory(call_id: str) -> _SlowBridge:
        bridge = _SlowBridge(call_id)
        bridges[call_id] = bridge
        return bridge

    monkeypatch.setattr(media_session, "MediaBridge", factory)
    _patch_settings(monkeypatch, welcome="", timeout=30.0, barge_in=True)
    _capture_logs(monkeypatch)
    _fake_tts(monkeypatch)
    _fake_agent(monkeypatch)

    async def fake_transcribe_stream(audio_chunks, *, call_id, sample_rate=None):
        yield TranscriptSegment(call_id=call_id, text="hello", is_final=True, ts=0.0)
        return
        yield  # pragma: no cover

    monkeypatch.setattr(media_session, "transcribe_stream", fake_transcribe_stream)

    await media_session.start_session("call-e", "+15559990001", "offer-e")
    await _await_session("call-e")

    assert bridges["call-e"].stopped is False
    assert [c.audio for c in bridges["call-e"].played] == [b"reply-hello"]
    assert bridges["call-e"].closed is True
