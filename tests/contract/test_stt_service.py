"""Contract tests for the Deepgram STT streaming service (T013).

Mocks the Deepgram client at the module boundary (``app.services.stt.AsyncDeepgramClient``)
so these tests exercise the real chunk-streaming / segment-yielding / error-mapping logic
with zero network access and no real API key. ``tests/conftest.py`` already seeds a dummy
``DEEPGRAM_API_KEY`` so ``get_settings()`` validates without real credentials.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from deepgram.core.events import EventType

from app.services.stt import SttError, transcribe_stream


def _message(
    transcript: str,
    *,
    is_final: bool = False,
    speech_final: bool = False,
    from_finalize: bool = False,
    confidence: float | None = None,
    word_confidences: list[float] | None = None,
) -> Any:
    """Build a fake Deepgram ``Results`` message (only the attributes stt.py reads)."""

    if word_confidences is not None:
        words = [SimpleNamespace(confidence=c) for c in word_confidences]
    else:
        words = [] if confidence is None else [SimpleNamespace(confidence=confidence)]
    alternative = SimpleNamespace(transcript=transcript, confidence=confidence, words=words)
    channel = SimpleNamespace(alternatives=[alternative])
    return SimpleNamespace(
        channel=channel,
        is_final=is_final,
        speech_final=speech_final,
        from_finalize=from_finalize,
    )


class _FakeConnection:
    """Stands in for Deepgram's ``AsyncV1SocketClient``: records sends, replays a script."""

    def __init__(self, script: list[Any], fail_in_listen: BaseException | None = None) -> None:
        self._script = script
        self._fail_in_listen = fail_in_listen
        self._callbacks: dict[EventType, list[Any]] = {}
        self.sent_chunks: list[bytes] = []
        self.keep_alives = 0
        self.finalized = False
        self.closed = False

    def on(self, event_type: EventType, callback: Any) -> None:
        self._callbacks.setdefault(event_type, []).append(callback)

    def _emit(self, event_type: EventType, data: Any) -> None:
        for callback in self._callbacks.get(event_type, []):
            callback(data)

    async def start_listening(self) -> None:
        self._emit(EventType.OPEN, None)
        if self._fail_in_listen is not None:
            self._emit(EventType.ERROR, self._fail_in_listen)
            self._emit(EventType.CLOSE, None)
            return
        for message in self._script:
            await asyncio.sleep(0)  # interleave with the sender task, like a real socket
            self._emit(EventType.MESSAGE, message)
        self._emit(EventType.CLOSE, None)

    async def send_media(self, chunk: bytes) -> None:
        self.sent_chunks.append(chunk)

    async def send_keep_alive(self) -> None:
        self.keep_alives += 1

    async def send_finalize(self) -> None:
        self.finalized = True

    async def send_close_stream(self) -> None:
        self.closed = True


def _fake_client(
    *,
    script: list[Any] | None = None,
    fail_on_connect: BaseException | None = None,
    fail_in_listen: BaseException | None = None,
) -> Any:
    """Build a fake object shaped like ``AsyncDeepgramClient(api_key=...)``."""

    @asynccontextmanager
    async def connect(**_kwargs: Any):
        if fail_on_connect is not None:
            raise fail_on_connect
        yield _FakeConnection(list(script or []), fail_in_listen)

    return SimpleNamespace(listen=SimpleNamespace(v1=SimpleNamespace(connect=connect)))


async def _audio(*chunks: bytes):
    for chunk in chunks:
        yield chunk


async def test_clear_audio_yields_final_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    """US2 AS1 / SC-006: clear audio produces a final segment matching the spoken words."""

    script = [_message("hello world", is_final=True, speech_final=True)]
    monkeypatch.setattr(
        "app.services.stt.AsyncDeepgramClient", lambda **_: _fake_client(script=script)
    )

    segments = [
        seg
        async for seg in transcribe_stream(_audio(b"\x00\x01", b"\x02\x03"), call_id="call-1")
    ]

    assert len(segments) == 1
    assert segments[0].call_id == "call-1"
    assert segments[0].text == "hello world"
    assert segments[0].is_final is True


async def test_silence_yields_empty_result_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """US2 AS2 / FR-011: silence-only audio returns an empty result, never an exception."""

    monkeypatch.setattr(
        "app.services.stt.AsyncDeepgramClient", lambda **_: _fake_client(script=[])
    )

    segments = [seg async for seg in transcribe_stream(_audio(b"\x00\x00"), call_id="call-2")]

    assert segments == []


async def test_provider_outage_raises_stt_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """US2 AS3 / FR-009: a provider connection failure surfaces as a typed ``SttError``."""

    monkeypatch.setattr(
        "app.services.stt.AsyncDeepgramClient",
        lambda **_: _fake_client(fail_on_connect=ConnectionError("deepgram unreachable")),
    )

    with pytest.raises(SttError):
        async for _ in transcribe_stream(_audio(b"\x00\x00"), call_id="call-3"):
            pass


async def test_mid_stream_error_raises_stt_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-009: an error emitted mid-stream (after a successful connect) also raises ``SttError``."""

    monkeypatch.setattr(
        "app.services.stt.AsyncDeepgramClient",
        lambda **_: _fake_client(fail_in_listen=RuntimeError("socket reset")),
    )

    with pytest.raises(SttError):
        async for _ in transcribe_stream(_audio(b"\x00\x00"), call_id="call-4"):
            pass


async def test_endpointing_config_passed_to_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """T013 / FR-005: silence-based endpointing + UtteranceEnd options reach ``connect``."""

    captured: dict[str, Any] = {}

    @asynccontextmanager
    async def connect(**kwargs: Any):
        captured.update(kwargs)
        yield _FakeConnection([], None)

    client = SimpleNamespace(listen=SimpleNamespace(v1=SimpleNamespace(connect=connect)))
    monkeypatch.setattr("app.services.stt.AsyncDeepgramClient", lambda **_: client)

    _ = [seg async for seg in transcribe_stream(_audio(b"\x00\x00"), call_id="call-5")]

    assert captured["endpointing"] == 800  # ~0.8 s clarified pause
    assert captured["utterance_end_ms"] == "1000"
    assert captured["vad_events"] is True
    assert captured["interim_results"] is True
    assert captured["language"] == "en-US"
    assert captured["smart_format"] is True
    assert "suite" in captured["keyterm"]
    assert "room availability" in captured["keyterm"]


async def test_final_segment_confidence_is_mean_not_min_across_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confidence is averaged across words, not the minimum: a clear sentence with one or two
    inherently-uncertain tokens (a name, a room number) stays above the clarification threshold
    instead of being rejected on its weakest word."""

    # "Norman Khan book 502": the name/number words score low, the rest high.
    words = [0.99, 0.5, 0.5, 0.95, 0.5]
    script = [_message("Norman Khan book 502", is_final=True, speech_final=True,
                       word_confidences=words)]
    monkeypatch.setattr(
        "app.services.stt.AsyncDeepgramClient", lambda **_: _fake_client(script=script)
    )

    segments = [seg async for seg in transcribe_stream(_audio(b"\x00"), call_id="call-confidence")]

    assert len(segments) == 1
    assert segments[0].confidence == pytest.approx(sum(words) / len(words))
    # min() would have been 0.5 (rejected); the mean clears the 0.5 default threshold.
    assert segments[0].confidence > min(words)


async def test_interim_segments_before_final_only_speech_final_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T014 / FR-012: interim (non-empty) segments are emitted; only ``speech_final`` finalizes."""

    script = [
        _message("book", is_final=False, speech_final=False),  # brief pause < 0.8 s → interim
        _message("book a table", is_final=False, speech_final=False),
        _message("book a table", is_final=True, speech_final=True),  # ~0.8 s pause → final
    ]
    monkeypatch.setattr(
        "app.services.stt.AsyncDeepgramClient", lambda **_: _fake_client(script=script)
    )

    segments = [seg async for seg in transcribe_stream(_audio(b"\x00"), call_id="call-6")]

    assert [seg.is_final for seg in segments] == [False, False, True]
    assert segments[-1].text == "book a table"
    # Non-empty interim segments were surfaced before the final (barge-in signal, FR-013).
    assert any(seg.text and not seg.is_final for seg in segments)


def _utterance_end() -> Any:
    """Build a fake Deepgram ``UtteranceEnd`` message (endpointing backstop, no transcript)."""

    return SimpleNamespace(type="UtteranceEnd", channel=None, last_word_end=1.5)


async def test_multi_segment_utterance_joined_and_finalized_on_speech_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-005: distinct ``is_final`` segments are accumulated and only ``speech_final`` ends the
    turn — with the FULL joined utterance, not just the last segment.

    ``is_final`` fires per finalized segment while the caller is still speaking; treating it as
    end-of-turn made the agent jump in mid-sentence. Regression guard for that fix.
    """

    script = [
        _message("I want to book a room", is_final=True, speech_final=False),  # segment 1
        _message("for next weekend", is_final=True, speech_final=True),  # segment 2 + endpoint
    ]
    monkeypatch.setattr(
        "app.services.stt.AsyncDeepgramClient", lambda **_: _fake_client(script=script)
    )

    segments = [seg async for seg in transcribe_stream(_audio(b"\x00"), call_id="call-7")]

    # The mid-utterance is_final stays interim; only speech_final finalizes, with both joined.
    assert [seg.is_final for seg in segments] == [False, True]
    assert segments[-1].text == "I want to book a room for next weekend"


async def test_keepalive_sent_while_inbound_audio_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent caller (DTX: no RTP frames) must not let Deepgram close the socket with
    NET-0001 — the sender emits KeepAlive messages while audio is stalled."""

    from app.services import stt

    monkeypatch.setattr(stt, "_KEEPALIVE_INTERVAL_S", 0.02)

    class _LiveConnection(_FakeConnection):
        """Stays open (like a real socket) until the sender sends CloseStream."""

        def __init__(self) -> None:
            super().__init__([], None)
            self._done = asyncio.Event()

        async def start_listening(self) -> None:
            self._emit(EventType.OPEN, None)
            await self._done.wait()
            self._emit(EventType.CLOSE, None)

        async def send_close_stream(self) -> None:
            await super().send_close_stream()
            self._done.set()

    connection_holder: list[_FakeConnection] = []

    @asynccontextmanager
    async def connect(**_kwargs: Any):
        connection = _LiveConnection()
        connection_holder.append(connection)
        yield connection

    client = SimpleNamespace(listen=SimpleNamespace(v1=SimpleNamespace(connect=connect)))
    monkeypatch.setattr("app.services.stt.AsyncDeepgramClient", lambda **_: client)

    async def stalled_audio():
        yield b"\x00\x01"
        await asyncio.sleep(0.1)  # several keepalive intervals with no audio
        yield b"\x02\x03"

    segments = [seg async for seg in transcribe_stream(stalled_audio(), call_id="call-ka")]

    assert segments == []
    connection = connection_holder[0]
    assert connection.sent_chunks == [b"\x00\x01", b"\x02\x03"]
    assert connection.keep_alives >= 2
    assert connection.finalized and connection.closed


async def test_utterance_end_finalizes_turn_when_no_speech_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-005 backstop: if endpointing never sets ``speech_final``, an ``UtteranceEnd`` message
    still ends the turn with the accumulated transcript (so the agent doesn't hang)."""

    script = [
        _message("book a room", is_final=True, speech_final=False),  # finalized, no speech_final
        _utterance_end(),  # endpointing backstop closes the turn
    ]
    monkeypatch.setattr(
        "app.services.stt.AsyncDeepgramClient", lambda **_: _fake_client(script=script)
    )

    segments = [seg async for seg in transcribe_stream(_audio(b"\x00"), call_id="call-8")]

    assert [seg.is_final for seg in segments] == [False, True]
    assert segments[-1].text == "book a room"


async def test_dtx_pause_finalizes_turn_via_forced_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DTX gap: the caller stops, RTP stalls and no ``speech_final`` arrives. After the idle
    threshold the sender issues a Finalize, and Deepgram's ``from_finalize`` reply ends the turn
    so the agent isn't left waiting forever (the "STT not listening" hang)."""

    from app.services import stt

    monkeypatch.setattr(stt, "_KEEPALIVE_INTERVAL_S", 5.0)
    monkeypatch.setattr(stt, "_ENDPOINT_IDLE_FINALIZE_S", 0.05)

    class _DtxConnection(_FakeConnection):
        """Emits an interim as audio flows, then a ``from_finalize`` result on Finalize."""

        def __init__(self) -> None:
            super().__init__([], None)
            self._done = asyncio.Event()
            self._finalize_emitted = False

        async def start_listening(self) -> None:
            self._emit(EventType.OPEN, None)
            await self._done.wait()
            self._emit(EventType.CLOSE, None)

        async def send_media(self, chunk: bytes) -> None:
            await super().send_media(chunk)
            # Live interim as the caller speaks — not yet finalized (no speech_final).
            self._emit(EventType.MESSAGE, _message("book a room", is_final=False))

        async def send_finalize(self) -> None:
            await super().send_finalize()
            if not self._finalize_emitted:  # real Deepgram flushes buffered audio just once
                self._finalize_emitted = True
                self._emit(
                    EventType.MESSAGE,
                    _message("book a room", is_final=True, from_finalize=True),
                )

        async def send_close_stream(self) -> None:
            await super().send_close_stream()
            self._done.set()

    holder: list[_FakeConnection] = []

    @asynccontextmanager
    async def connect(**_kwargs: Any):
        connection = _DtxConnection()
        holder.append(connection)
        yield connection

    client = SimpleNamespace(listen=SimpleNamespace(v1=SimpleNamespace(connect=connect)))
    monkeypatch.setattr("app.services.stt.AsyncDeepgramClient", lambda **_: client)

    async def dtx_audio():
        yield b"\x00\x01"
        await asyncio.sleep(0.2)  # caller paused; DTX suppresses all further RTP frames

    segments = [seg async for seg in transcribe_stream(dtx_audio(), call_id="call-dtx")]

    # The turn must have been finalized (a FINAL segment) despite no speech_final ever arriving.
    assert any(seg.is_final for seg in segments)
    assert segments[-1].text == "book a room"
    assert holder[0].finalized
