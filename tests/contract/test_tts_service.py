"""Rollback contract tests for the retained Cartesia TTS implementation (T015 for US3).

Exercises the retained Cartesia rollback function by mocking the async client at the
module boundary (`app.services.tts.AsyncCartesia`) —
no network calls, no real API key needed. `Settings` picks up the dummy feature-003 secrets
(`CARTESIA_API_KEY`, etc.) that `tests/conftest.py` sets globally, so `get_settings()`
validates without any real credentials.

Fakes below stand in for the real cartesia 3.3.0 SDK shapes verified in
`src/app/services/tts.py`: `AsyncCartesia().tts.websocket_connect()` ->
`AsyncTTSResourceConnectionManager` (async context manager) -> `.context(...)` ->
`AsyncWebSocketContext` with `push()` / `no_more_inputs()` / `receive()`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from cartesia import CartesiaError

from app.core.config import get_settings
from app.services import tts
from app.services.tts import TtsError


class _FakeChunkEvent:
    """Stands in for `cartesia.types.websocket_response.Chunk`."""

    type = "chunk"

    def __init__(self, audio: bytes) -> None:
        self.audio = audio


class _FakeErrorEvent:
    """Stands in for `cartesia.types.websocket_response.Error`."""

    type = "error"

    def __init__(self, message: str) -> None:
        self.message = message


class _FakeContext:
    """Stands in for `AsyncWebSocketContext`."""

    def __init__(self, events: list[object]) -> None:
        self._events = events
        self.pushed: list[str] = []
        self.completed = False

    async def push(self, text: str) -> None:
        self.pushed.append(text)

    async def no_more_inputs(self) -> None:
        self.completed = True

    async def receive(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event


class _FakeConnection:
    """Stands in for `AsyncTTSResourceConnection` (yielded by the manager's `__aenter__`)."""

    def __init__(self, events: list[object]) -> None:
        self._events = events
        self.contexts: list[_FakeContext] = []
        self.context_kwargs: list[dict] = []

    def context(self, **kwargs: object) -> _FakeContext:
        self.context_kwargs.append(kwargs)
        ctx = _FakeContext(self._events)
        self.contexts.append(ctx)
        return ctx


class _FakeConnectionManager:
    """Stands in for `AsyncTTSResourceConnectionManager` returned by `tts.websocket_connect()`."""

    def __init__(
        self,
        events: list[object] | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self._events = events or []
        self._connect_error = connect_error
        self.connection: _FakeConnection | None = None

    async def __aenter__(self) -> _FakeConnection:
        if self._connect_error is not None:
            raise self._connect_error
        self.connection = _FakeConnection(self._events)
        return self.connection

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeTtsResource:
    def __init__(self, manager: _FakeConnectionManager) -> None:
        self._manager = manager

    def websocket_connect(self) -> _FakeConnectionManager:
        return self._manager


class _FakeAsyncCartesia:
    """Stands in for `AsyncCartesia` — tracks `close()` so tests can assert cleanup ran."""

    def __init__(self, manager: _FakeConnectionManager) -> None:
        self.tts = _FakeTtsResource(manager)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, manager: _FakeConnectionManager
) -> _FakeAsyncCartesia:
    fake_client = _FakeAsyncCartesia(manager)
    monkeypatch.setattr(tts, "AsyncCartesia", lambda **kwargs: fake_client)
    return fake_client


@pytest.mark.asyncio
async def test_synthesize_stream_yields_audio_for_nonempty_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """US3 AS1: non-empty reply text streams back audio SpeechChunks."""

    events = [_FakeChunkEvent(b"\x00\x01"), _FakeChunkEvent(b"\x02\x03")]
    manager = _FakeConnectionManager(events=events)
    fake_client = _patch_client(monkeypatch, manager)

    chunks = [c async for c in tts._synthesize_stream_cartesia("Hello there", call_id="call-1")]

    assert len(chunks) == 2
    assert [c.audio for c in chunks] == [b"\x00\x01", b"\x02\x03"]
    assert all(c.call_id == "call-1" for c in chunks)
    # Output rate matches the outbound Opus wire rate (feature 004, research.md §1).
    assert all(c.sample_rate == 48000 for c in chunks)
    assert get_settings().tts_output_sample_rate == 48000

    assert manager.connection is not None
    ctx = manager.connection.contexts[0]
    assert ctx.pushed == ["Hello there"]
    assert ctx.completed is True
    assert fake_client.closed is True
    # Managed buffering is enabled so token-by-token pushes yield clear, natural speech.
    kwargs = manager.connection.context_kwargs[0]
    assert kwargs["max_buffer_delay_ms"] == get_settings().cartesia_max_buffer_delay_ms
    assert kwargs["max_buffer_delay_ms"] > 0


@pytest.mark.asyncio
async def test_synthesize_stream_incremental_input_skips_empty_pieces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incremental (async-iterator) input is pushed piece by piece; empty pieces are skipped."""

    async def pieces() -> AsyncIterator[str]:
        yield "Hello"
        yield ""
        yield " world"

    manager = _FakeConnectionManager(events=[_FakeChunkEvent(b"\xff")])
    _patch_client(monkeypatch, manager)

    chunks = [c async for c in tts._synthesize_stream_cartesia(pieces(), call_id="call-2")]

    assert len(chunks) == 1
    assert manager.connection is not None
    assert manager.connection.contexts[0].pushed == ["Hello", " world"]


@pytest.mark.asyncio
async def test_synthesize_stream_empty_text_yields_no_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """US3 AS2 / FR-012: empty input -> no audio, no crash; reported via zero chunks emitted."""

    manager = _FakeConnectionManager(events=[_FakeChunkEvent(b"should-not-be-used")])
    _patch_client(monkeypatch, manager)

    chunks = [c async for c in tts._synthesize_stream_cartesia("", call_id="call-3")]

    assert chunks == []
    # The websocket must never even be opened for empty input.
    assert manager.connection is None


@pytest.mark.asyncio
async def test_synthesize_stream_starts_before_last_piece_pushed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T019 / FR-008: the first audio chunk streams back before the final text piece is pushed."""

    allow_last = asyncio.Event()

    async def pieces() -> AsyncIterator[str]:
        yield "A"
        await allow_last.wait()  # gate the final piece until the first chunk has arrived
        yield "B"

    manager = _FakeConnectionManager(events=[_FakeChunkEvent(b"\x01"), _FakeChunkEvent(b"\x02")])
    _patch_client(monkeypatch, manager)

    chunks = []
    async for chunk in tts._synthesize_stream_cartesia(pieces(), call_id="call-early"):
        chunks.append(chunk)
        if len(chunks) == 1:
            # First audio is already flowing while "B" is still gated (not yet pushed).
            assert manager.connection is not None
            assert "B" not in manager.connection.contexts[0].pushed
            allow_last.set()

    assert [c.audio for c in chunks] == [b"\x01", b"\x02"]
    # Both pieces were ultimately pushed, in order, and the input was closed.
    ctx = manager.connection.contexts[0]
    assert ctx.pushed == ["A", "B"]
    assert ctx.completed is True


@pytest.mark.asyncio
async def test_synthesize_stream_raises_tts_error_on_provider_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """US3 AS3 / FR-009: provider unavailable surfaces a typed TtsError, never hangs."""

    manager = _FakeConnectionManager(
        connect_error=CartesiaError("simulated outage: connection refused")
    )
    fake_client = _patch_client(monkeypatch, manager)

    with pytest.raises(TtsError):
        async for _ in tts._synthesize_stream_cartesia("Hello", call_id="call-4"):
            pass

    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_synthesize_stream_raises_tts_error_on_mid_stream_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cartesia `error` event mid-stream is surfaced as TtsError, not swallowed."""

    events = [_FakeChunkEvent(b"\x01"), _FakeErrorEvent("synthesis failed")]
    manager = _FakeConnectionManager(events=events)
    _patch_client(monkeypatch, manager)

    received = []
    with pytest.raises(TtsError):
        async for chunk in tts._synthesize_stream_cartesia("Hello", call_id="call-5"):
            received.append(chunk)

    assert len(received) == 1  # the chunk before the error event was still yielded
