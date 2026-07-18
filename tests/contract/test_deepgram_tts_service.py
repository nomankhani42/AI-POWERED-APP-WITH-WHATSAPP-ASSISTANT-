"""Contract tests for the active Deepgram Aura streaming TTS provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from deepgram.speak.v1.types import SpeakV1Flushed

from app.core.config import get_settings
from app.services import tts
from app.services.tts import TtsError


class _FakeConnection:
    def __init__(
        self,
        events: list[bytes] | None = None,
        stream_error: Exception | None = None,
        stall_before_events: bool = False,
    ) -> None:
        self.events = events or []
        self.stream_error = stream_error
        self.stall_before_events = stall_before_events
        self.sent_text: list[str] = []
        self.flushed = False
        self.cleared = False
        self.closed = False
        self._flush_event = asyncio.Event()

    async def send_text(self, message: object) -> None:
        self.sent_text.append(message.text)

    async def send_flush(self) -> None:
        self.flushed = True
        self._flush_event.set()

    async def send_clear(self) -> None:
        self.cleared = True

    async def send_close(self) -> None:
        self.closed = True
        self._flush_event.set()

    async def __aiter__(self) -> AsyncIterator[object]:
        if self.stall_before_events:
            await asyncio.Event().wait()
        for event in self.events:
            yield event
        if self.stream_error is not None:
            raise self.stream_error
        await self._flush_event.wait()
        yield SpeakV1Flushed(type="Flushed", sequence_id=1)


class _FakeConnectionManager:
    def __init__(
        self,
        connection: _FakeConnection,
        connect_error: Exception | None = None,
    ) -> None:
        self.connection = connection
        self.connect_error = connect_error
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self) -> _FakeConnection:
        self.enter_count += 1
        if self.connect_error is not None:
            raise self.connect_error
        return self.connection

    async def __aexit__(self, *exc_info: object) -> bool:
        self.exit_count += 1
        return False


class _FakeV1:
    def __init__(self, manager: _FakeConnectionManager) -> None:
        self.manager = manager
        self.connect_kwargs: dict[str, object] | None = None

    def connect(self, **kwargs: object) -> _FakeConnectionManager:
        self.connect_kwargs = kwargs
        return self.manager


class _FakeSpeak:
    def __init__(self, v1: _FakeV1) -> None:
        self.v1 = v1


class _FakeDeepgramClient:
    def __init__(self, v1: _FakeV1) -> None:
        self.speak = _FakeSpeak(v1)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[bytes] | None = None,
    stream_error: Exception | None = None,
    connect_error: Exception | None = None,
    stall_before_events: bool = False,
) -> tuple[_FakeConnection, _FakeV1]:
    connection = _FakeConnection(
        events=events,
        stream_error=stream_error,
        stall_before_events=stall_before_events,
    )
    v1 = _FakeV1(_FakeConnectionManager(connection, connect_error=connect_error))
    client = _FakeDeepgramClient(v1)
    monkeypatch.setattr(tts, "AsyncDeepgramClient", lambda **_: client)
    return connection, v1


@pytest.mark.asyncio
async def test_synthesize_stream_yields_deepgram_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, v1 = _patch_client(
        monkeypatch,
        events=[b"\x00\x01", b"\x02\x03"],
    )

    chunks = [chunk async for chunk in tts.synthesize_stream("Hello", call_id="call-1")]

    assert [chunk.audio for chunk in chunks] == [b"\x00\x01", b"\x02\x03"]
    assert all(chunk.call_id == "call-1" for chunk in chunks)
    assert all(chunk.sample_rate == 48000 for chunk in chunks)
    assert connection.sent_text == ["Hello"]
    assert connection.flushed is True
    assert connection.closed is True
    assert v1.connect_kwargs == {
        "model": get_settings().deepgram_tts_model,
        "encoding": "linear16",
        "sample_rate": 48000,
        "speed": get_settings().deepgram_tts_speed,
    }


@pytest.mark.asyncio
async def test_synthesize_stream_forwards_incremental_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def pieces() -> AsyncIterator[str]:
        yield "Hello"
        yield ""
        yield " world"

    connection, _ = _patch_client(monkeypatch, events=[b"audio"])

    chunks = [chunk async for chunk in tts.synthesize_stream(pieces(), call_id="call-2")]

    assert [chunk.audio for chunk in chunks] == [b"audio"]
    assert connection.sent_text == ["Hello world"]


@pytest.mark.asyncio
async def test_synthesize_stream_empty_text_opens_no_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def make_client(**_: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("Deepgram client must not be created for empty input")

    monkeypatch.setattr(tts, "AsyncDeepgramClient", make_client)

    chunks = [chunk async for chunk in tts.synthesize_stream("", call_id="call-3")]

    assert chunks == []
    assert called is False


@pytest.mark.asyncio
async def test_synthesize_stream_emits_audio_before_all_text_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allow_last = asyncio.Event()

    async def pieces() -> AsyncIterator[str]:
        yield "A."
        await allow_last.wait()
        yield "B"

    connection, _ = _patch_client(monkeypatch, events=[b"first"])
    received = []

    async for chunk in tts.synthesize_stream(pieces(), call_id="call-early"):
        received.append(chunk)
        assert connection.sent_text in ([], ["A."])
        allow_last.set()

    assert [chunk.audio for chunk in received] == [b"first"]
    assert connection.sent_text == ["A.", "B"]


@pytest.mark.asyncio
async def test_synthesize_stream_wraps_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, connect_error=ConnectionError("simulated outage"))

    with pytest.raises(TtsError, match="Deepgram TTS failed"):
        async for _ in tts.synthesize_stream("Hello", call_id="call-4"):
            pass


@pytest.mark.asyncio
async def test_synthesize_stream_wraps_midstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, _ = _patch_client(
        monkeypatch,
        events=[b"first"],
        stream_error=ConnectionError("socket closed"),
    )
    received = []

    with pytest.raises(TtsError, match="Deepgram TTS failed"):
        async for chunk in tts.synthesize_stream("Hello", call_id="call-5"):
            received.append(chunk)

    assert [chunk.audio for chunk in received] == [b"first"]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_synthesize_stream_splits_oversized_speak_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = ("A" * 1900) + " " + ("B" * 1900)
    connection, _ = _patch_client(monkeypatch, events=[b"audio"])

    chunks = [chunk async for chunk in tts.synthesize_stream(text, call_id="call-long")]

    assert [chunk.audio for chunk in chunks] == [b"audio"]
    assert "".join(connection.sent_text) == text
    assert len(connection.sent_text) > 1
    assert all(len(piece) <= tts._DEEPGRAM_TEXT_CHUNK_CHARS for piece in connection.sent_text)


@pytest.mark.asyncio
async def test_synthesize_stream_times_out_and_closes_stalled_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(update={"tts_first_audio_timeout_s": 0.01})
    monkeypatch.setattr(tts, "get_settings", lambda: settings)
    connection, _ = _patch_client(monkeypatch, stall_before_events=True)

    with pytest.raises(TtsError, match="timed out waiting for first audio"):
        async for _ in tts.synthesize_stream("Hello", call_id="call-timeout"):
            pass

    assert connection.cleared is True
    assert connection.closed is True


@pytest.mark.asyncio
async def test_synthesize_stream_rejects_flush_with_no_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Flushed response without PCM must not be reported as audible playback."""

    connection, _ = _patch_client(monkeypatch, events=[])

    with pytest.raises(TtsError, match="returned no audio"):
        async for _ in tts.synthesize_stream("Hello", call_id="call-no-audio"):
            pass

    assert connection.cleared is True
    assert connection.closed is True


@pytest.mark.asyncio
async def test_warm_opens_and_reuses_socket_for_first_utterance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, v1 = _patch_client(monkeypatch, events=[b"audio"])
    session = tts.DeepgramTtsSession("call-warm")

    await session.warm()
    chunks = [chunk async for chunk in session.synthesize_stream("Welcome.")]

    assert [chunk.audio for chunk in chunks] == [b"audio"]
    assert v1.manager.enter_count == 1
    assert connection.sent_text == ["Welcome."]

    await session.close()


@pytest.mark.asyncio
async def test_deepgram_session_reuses_one_socket_for_multiple_utterances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection, v1 = _patch_client(monkeypatch, events=[b"audio"])
    session = tts.DeepgramTtsSession("call-persistent")

    first = [chunk async for chunk in session.synthesize_stream("Hello.")]
    second = [chunk async for chunk in session.synthesize_stream("Welcome back.")]

    assert [chunk.audio for chunk in first] == [b"audio"]
    assert [chunk.audio for chunk in second] == [b"audio"]
    assert v1.manager.enter_count == 1
    assert connection.sent_text == ["Hello.", "Welcome back."]
    assert connection.closed is False

    await session.close()

    assert connection.closed is True
    assert v1.manager.exit_count == 1


@pytest.mark.asyncio
async def test_reused_socket_failure_retries_once_on_fresh_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deepgram closes idle Speak sockets between turns. A reused socket failing before any
    audio must transparently reconnect and replay the utterance, not surface a TtsError."""

    stale = _FakeConnection(events=[b"first-audio"])
    fresh = _FakeConnection(events=[b"retry-audio"])

    class _SequenceV1:
        def __init__(self) -> None:
            self.managers = [_FakeConnectionManager(stale), _FakeConnectionManager(fresh)]
            self.connect_count = 0

        def connect(self, **_kwargs: object) -> _FakeConnectionManager:
            manager = self.managers[self.connect_count]
            self.connect_count += 1
            return manager

    v1 = _SequenceV1()
    monkeypatch.setattr(tts, "AsyncDeepgramClient", lambda **_: _FakeDeepgramClient(v1))

    session = tts.DeepgramTtsSession("call-retry")
    first = [chunk async for chunk in session.synthesize_stream("Hello.")]
    assert [chunk.audio for chunk in first] == [b"first-audio"]

    # The idle socket dies between turns: its event stream now fails immediately.
    stale.events = []
    stale.stream_error = ConnectionError("closed by idle timeout")

    second = [chunk async for chunk in session.synthesize_stream("Second.")]

    assert [chunk.audio for chunk in second] == [b"retry-audio"]
    assert fresh.sent_text == ["Second."]  # utterance replayed in full on the new socket
    assert v1.connect_count == 2
    assert stale.closed is True  # dead socket was discarded

    await session.close()


@pytest.mark.asyncio
async def test_streaming_deltas_are_coalesced_at_sentence_boundaries() -> None:
    async def pieces() -> AsyncIterator[str]:
        yield "Hello"
        yield " world. "
        yield "This is"
        yield " natural."

    chunks = [chunk async for chunk in tts._iter_deepgram_text(pieces())]

    assert chunks == ["Hello world. ", "This is natural."]
    assert "".join(chunks) == "Hello world. This is natural."
