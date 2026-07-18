"""Text-to-speech service — Deepgram Aura streaming behind a stable interface.

Single job: turn assistant reply text into streamed speech audio (FR-008, FR-010).
Provider is swappable behind ``synthesize_stream`` (constitution Principle IV); callers
(the media bridge) never touch either provider SDK directly. See
``specs/003-voice-call-webhook/contracts/tts-service.md`` for the contract this
implements.

Deepgram Aura is the active calling-agent provider. The previous Cartesia implementation
is intentionally retained as ``_synthesize_stream_cartesia`` for a quick rollback.

The retained implementation was verified against the installed ``cartesia==3.3.0`` SDK:
``AsyncCartesia().tts.websocket_connect()`` returns an async-context-manager
``AsyncTTSResourceConnectionManager`` whose ``__aenter__`` yields an
``AsyncTTSResourceConnection``. ``.context(model_id=..., voice=..., output_format=...,
language=...)`` on that connection returns an ``AsyncWebSocketContext`` with
``await ctx.push(text)`` (send incremental transcript), ``await ctx.no_more_inputs()``
(signal end of input), and ``async for event in ctx.receive(): ...`` (drain this
context's response queue). Response events are a discriminated union on ``event.type``:
``"chunk"`` (has a decoded-bytes ``.audio`` property), ``"error"`` (has ``.message``),
``"done"``. All SDK exceptions share the ``cartesia.CartesiaError`` base.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from cartesia import AsyncCartesia, CartesiaError
from deepgram import AsyncDeepgramClient
from deepgram.speak.v1.types import SpeakV1Flushed, SpeakV1Text, SpeakV1Warning

from app.core.config import get_settings
from app.services.media.types import SpeechChunk

logger = logging.getLogger(__name__)

# Stay below Deepgram Aura's 2,000-character limit for one Speak message.
_DEEPGRAM_TEXT_CHUNK_CHARS = 1800
# Deepgram recommends sentence-sized chunks, or roughly 50-100 characters for voice
# assistants. These bounds are a latency fallback for a long sentence without punctuation.
_DEEPGRAM_TARGET_CHUNK_CHARS = 100
_DEEPGRAM_MIN_CHUNK_CHARS = 50
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])(?:\s+|$)")


class TtsError(Exception):
    """Raised when the active TTS provider fails or errors mid-stream (FR-009)."""


async def _iter_text(text_chunks: AsyncIterator[str] | str) -> AsyncIterator[str]:
    """Normalize the accepted input shapes into a stream of non-empty text pieces."""

    if isinstance(text_chunks, str):
        if text_chunks:
            yield text_chunks
        return
    async for chunk in text_chunks:
        if chunk:
            yield chunk


def _split_deepgram_text(text: str) -> list[str]:
    """Split oversized text at whitespace without changing the spoken content."""

    chunks: list[str] = []
    remaining = text
    while len(remaining) > _DEEPGRAM_TEXT_CHUNK_CHARS:
        cut = remaining.rfind(" ", 0, _DEEPGRAM_TEXT_CHUNK_CHARS + 1)
        if cut <= 0:
            cut = _DEEPGRAM_TEXT_CHUNK_CHARS
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _take_natural_chunk(buffer: str, *, final: bool) -> tuple[str, str] | None:
    """Take one sentence/clause-sized chunk while preserving the exact input text."""

    sentence_end = _SENTENCE_END_RE.search(buffer)
    if sentence_end is not None and sentence_end.end() <= _DEEPGRAM_TARGET_CHUNK_CHARS:
        cut = sentence_end.end()
        return buffer[:cut], buffer[cut:]

    if len(buffer) >= _DEEPGRAM_TARGET_CHUNK_CHARS:
        window = buffer[_DEEPGRAM_MIN_CHUNK_CHARS : _DEEPGRAM_TARGET_CHUNK_CHARS + 1]
        separators = [
            index
            for index, character in enumerate(window, start=_DEEPGRAM_MIN_CHUNK_CHARS)
            if character.isspace() or character in ",;:"
        ]
        cut = separators[-1] + 1 if separators else _DEEPGRAM_TARGET_CHUNK_CHARS
        return buffer[:cut], buffer[cut:]

    if final and buffer:
        return buffer, ""
    return None


async def _iter_deepgram_text(
    text_chunks: AsyncIterator[str] | str,
) -> AsyncIterator[str]:
    """Coalesce LLM deltas into natural Speak messages without changing their text."""

    buffer = ""
    async for piece in _iter_text(text_chunks):
        buffer += piece
        while result := _take_natural_chunk(buffer, final=False):
            chunk, buffer = result
            for bounded in _split_deepgram_text(chunk):
                yield bounded
    while result := _take_natural_chunk(buffer, final=True):
        chunk, buffer = result
        for bounded in _split_deepgram_text(chunk):
            yield bounded


class DeepgramTtsSession:
    """One reusable Deepgram Speak WebSocket owned by a single live call."""

    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self._client: AsyncDeepgramClient | None = None
        self._manager: Any = None
        self._connection: Any = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> DeepgramTtsSession:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    @staticmethod
    def _connection_closed(connection: Any) -> bool:
        """Whether the underlying websocket was closed remotely (e.g. Deepgram idle timeout)."""

        ws = getattr(connection, "_websocket", None)
        if ws is None:
            return False
        closed = getattr(ws, "closed", None)  # websockets legacy protocol
        if closed is not None:
            return bool(closed)
        state = getattr(ws, "state", None)  # websockets >= 13 asyncio client
        return getattr(state, "name", "") in {"CLOSING", "CLOSED"}

    async def _connect(self) -> Any:
        if self._connection is not None:
            if not self._connection_closed(self._connection):
                return self._connection
            # Deepgram closes an idle Speak socket between turns. Reconnect transparently
            # instead of failing the caller's next reply on a dead connection.
            logger.info(
                "Deepgram TTS socket closed remotely; reconnecting call_id=%s", self.call_id
            )
            await self._discard_connection()

        settings = get_settings()
        self._client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)
        self._manager = self._client.speak.v1.connect(
            model=settings.deepgram_tts_model,
            encoding="linear16",
            sample_rate=settings.tts_output_sample_rate,
            speed=settings.deepgram_tts_speed,
        )
        self._connection = await self._manager.__aenter__()
        logger.debug("Deepgram TTS connected call_id=%s", self.call_id)
        return self._connection

    async def warm(self) -> None:
        """Open the per-call socket before the first utterance to hide handshake latency."""

        async with self._lock:
            started_at = time.monotonic()
            await self._connect()
            logger.info(
                "Deepgram TTS ready call_id=%s connect_ms=%.1f",
                self.call_id,
                (time.monotonic() - started_at) * 1000,
            )

    async def _discard_connection(self) -> None:
        """Abandon a failed/interrupted stream so later speech reconnects cleanly."""

        connection, manager = self._connection, self._manager
        self._connection = None
        self._manager = None
        self._client = None
        if connection is not None:
            with suppress(Exception):
                await connection.send_clear()
            with suppress(Exception):
                await connection.send_close()
        if manager is not None:
            with suppress(Exception):
                await manager.__aexit__(None, None, None)

    async def close(self) -> None:
        """Gracefully close this call's persistent Speak WebSocket."""

        async with self._lock:
            connection, manager = self._connection, self._manager
            self._connection = None
            self._manager = None
            self._client = None
            if connection is not None:
                with suppress(Exception):
                    await connection.send_close()
            if manager is not None:
                with suppress(Exception):
                    await manager.__aexit__(None, None, None)
            if connection is not None:
                logger.debug("Deepgram TTS disconnected call_id=%s", self.call_id)

    async def synthesize_stream(
        self,
        text_chunks: AsyncIterator[str] | str,
    ) -> AsyncIterator[SpeechChunk]:
        """Synthesize one utterance while retaining the socket for the next turn.

        A reused socket can have been closed by Deepgram (idle timeout) in a way only
        discovered mid-send. When that first attempt fails before ANY audio arrived, the
        utterance is retried once on a fresh connection, replaying the text pieces already
        consumed. A fresh connection failing is a genuine provider error and is not retried.
        """

        source = _iter_deepgram_text(text_chunks)
        try:
            first = await source.__anext__()
        except StopAsyncIteration:
            return

        consumed: list[str] = [first]
        # In-flight fetch of the next source piece. Shielded so a failed first attempt being
        # torn down cannot cancel into (and thereby kill) the shared source generator — the
        # retry resumes from this same fetch and no piece is lost or duplicated.
        pending_fetch: asyncio.Task[str] | None = None

        async def _pieces(snapshot: list[str]) -> AsyncIterator[str]:
            nonlocal pending_fetch
            for piece in snapshot:
                yield piece
            while True:
                if pending_fetch is None:
                    pending_fetch = asyncio.ensure_future(source.__anext__())
                try:
                    piece = await asyncio.shield(pending_fetch)
                except StopAsyncIteration:
                    pending_fetch = None
                    return
                except asyncio.CancelledError:
                    if pending_fetch.cancelled():
                        pending_fetch = None
                    raise
                pending_fetch = None
                consumed.append(piece)
                yield piece

        try:
            async with self._lock:
                for attempt in (0, 1):
                    reused = self._connection is not None
                    stream = self._synthesize_once(_pieces(list(consumed)))
                    try:
                        first_chunk = await stream.__anext__()
                    except StopAsyncIteration:
                        return
                    except TtsError:
                        if reused and attempt == 0:
                            logger.warning(
                                "Deepgram TTS reused socket failed before audio; retrying on "
                                "a fresh connection call_id=%s",
                                self.call_id,
                            )
                            continue
                        raise
                    try:
                        yield first_chunk
                        async for chunk in stream:
                            yield chunk
                    finally:
                        await stream.aclose()
                    return
        finally:
            if pending_fetch is not None:
                pending_fetch.cancel()
                await asyncio.gather(pending_fetch, return_exceptions=True)

    async def _synthesize_once(
        self,
        pieces: AsyncIterator[str],
    ) -> AsyncIterator[SpeechChunk]:
        """One synthesis attempt over an already-coalesced piece stream (lock held)."""

        settings = get_settings()
        output_sample_rate = settings.tts_output_sample_rate
        push_task: asyncio.Task[None] | None = None
        try:
            connection = await self._connect()

            async def _push_all() -> None:
                try:
                    async for chunk in pieces:
                        await connection.send_text(SpeakV1Text(text=chunk))
                    await connection.send_flush()
                except BaseException:
                    with suppress(Exception):
                        await connection.send_close()
                    raise

            push_task = asyncio.create_task(_push_all())
            started_at = time.monotonic()
            first_audio_deadline = started_at + settings.tts_first_audio_timeout_s
            first_audio_received = False
            audio_chunks = 0
            audio_bytes = 0
            events = connection.__aiter__()

            while True:
                if first_audio_received:
                    event_timeout = settings.tts_event_timeout_s
                else:
                    event_timeout = max(0.001, first_audio_deadline - time.monotonic())
                try:
                    async with asyncio.timeout(event_timeout):
                        event = await anext(events)
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    phase = "audio stream" if first_audio_received else "first audio"
                    raise TtsError(
                        f"Deepgram TTS timed out waiting for {phase} for call {self.call_id}"
                    ) from exc

                if isinstance(event, bytes):
                    if event:
                        if not first_audio_received:
                            first_audio_received = True
                            logger.info(
                                "Deepgram TTS first audio call_id=%s model=%s latency_ms=%.1f",
                                self.call_id,
                                settings.deepgram_tts_model,
                                (time.monotonic() - started_at) * 1000,
                            )
                        audio_chunks += 1
                        audio_bytes += len(event)
                        yield SpeechChunk(
                            call_id=self.call_id,
                            audio=event,
                            sample_rate=output_sample_rate,
                        )
                elif isinstance(event, SpeakV1Warning):
                    logger.warning(
                        "Deepgram TTS warning for call %s (%s): %s",
                        self.call_id,
                        event.code,
                        event.description,
                    )
                elif isinstance(event, SpeakV1Flushed):
                    break

            push_result = (await asyncio.gather(push_task, return_exceptions=True))[0]
            if isinstance(push_result, BaseException) and not isinstance(
                push_result, asyncio.CancelledError
            ):
                raise TtsError(
                    f"Deepgram TTS push failed for call {self.call_id}: {push_result}"
                ) from push_result
            logger.debug(
                "Deepgram TTS utterance complete call_id=%s chunks=%d bytes=%d",
                self.call_id,
                audio_chunks,
                audio_bytes,
            )
            if not first_audio_received:
                # A successful Flush with no binary audio is not successful synthesis.
                # Treating it as success makes the media session believe the welcome or
                # reply played even though the caller heard silence.
                raise TtsError(
                    f"Deepgram TTS returned no audio for call {self.call_id}"
                )
        except BaseException as exc:
            if push_task is not None and not push_task.done():
                push_task.cancel()
            if push_task is not None:
                await asyncio.gather(push_task, return_exceptions=True)
            await self._discard_connection()
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit, TtsError)):
                raise
            raise TtsError(
                f"Deepgram TTS failed for call {self.call_id}: {exc}"
            ) from exc


async def synthesize_stream(
    text_chunks: AsyncIterator[str] | str,
    *,
    call_id: str,
) -> AsyncIterator[SpeechChunk]:
    """Synthesize one standalone utterance; call sessions should reuse DeepgramTtsSession."""

    async with DeepgramTtsSession(call_id) as session:
        async for chunk in session.synthesize_stream(text_chunks):
            yield chunk


# Cartesia rollback path. Kept intact and intentionally not called by the calling agent.
async def _synthesize_stream_cartesia(
    text_chunks: AsyncIterator[str] | str,
    *,
    call_id: str,
) -> AsyncIterator[SpeechChunk]:
    """Synthesize assistant reply text with the previous Cartesia implementation.

    Consumes ``text_chunks`` (a whole string or an async stream of incremental pieces as
    produced by ``run_turn``), opens a Cartesia Sonic websocket context, pushes each piece
    as it arrives, and yields a ``SpeechChunk`` per audio chunk as it streams back — so
    playback can start before the full reply has been synthesized.

    Empty input (no non-empty pieces) yields no chunks and returns normally: the
    empty-input outcome (FR-012) is reported to the caller simply by there being zero
    ``SpeechChunk``s, never invalid audio or an exception. Provider/connection failures,
    timeouts, or an ``"error"`` event mid-stream are surfaced as ``TtsError`` (FR-009) so
    the call can degrade gracefully instead of stalling.
    """

    settings = get_settings()
    output_sample_rate = settings.tts_output_sample_rate

    # Peek the first non-empty piece so empty input opens no websocket (FR-012), while still
    # supporting an async input stream we must not fully drain before synthesizing.
    source = _iter_text(text_chunks)
    try:
        first = await source.__anext__()
    except StopAsyncIteration:
        return

    client = AsyncCartesia(api_key=settings.cartesia_api_key)
    try:
        async with client.tts.websocket_connect() as ws:
            ctx = ws.context(
                model_id=settings.cartesia_model,
                voice={"mode": "id", "id": settings.cartesia_voice_id},
                output_format={
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    # Synthesize at the outbound wire rate (48 kHz) so the media bridge
                    # repacketizes into fixed 20 ms Opus frames with no resample seam — the
                    # fix for the garbled "old TV" playback (research.md §1).
                    "sample_rate": output_sample_rate,
                },
                language="en",
                # Managed buffering: we push LLM token deltas one at a time, so without this
                # Cartesia would synthesize each tiny fragment (even partial words) on its own,
                # producing choppy, unclear speech. This makes it buffer text until it has
                # enough context for natural prosody (Cartesia's recommended mode for
                # token-by-token streaming). ``no_more_inputs()`` still flushes the tail.
                max_buffer_delay_ms=settings.cartesia_max_buffer_delay_ms,
            )

            async def _push_all() -> None:
                # Push each piece the moment it arrives so Cartesia can start synthesizing from
                # the first tokens while the agent is still generating the rest (FR-008).
                await ctx.push(first)
                async for piece in source:
                    await ctx.push(piece)
                await ctx.no_more_inputs()

            push_task = asyncio.create_task(_push_all())
            try:
                async for event in ctx.receive():
                    if event.type == "error":
                        raise TtsError(
                            f"Cartesia TTS error for call {call_id}: {event.message}"
                        )
                    if event.type == "chunk" and event.audio:
                        yield SpeechChunk(
                            call_id=call_id,
                            audio=event.audio,
                            sample_rate=output_sample_rate,
                        )
            except BaseException:
                # Error, or the consumer stopped early (e.g. barge-in cancel): abandon pushing.
                if not push_task.done():
                    push_task.cancel()
                await asyncio.gather(push_task, return_exceptions=True)
                raise

            # Normal end of audio: let the remaining pushes + no_more_inputs finish so the
            # provider isn't left with a half-open context, and surface any push failure.
            push_result = (await asyncio.gather(push_task, return_exceptions=True))[0]
            if isinstance(push_result, BaseException) and not isinstance(
                push_result, asyncio.CancelledError
            ):
                raise TtsError(
                    f"Cartesia TTS push failed for call {call_id}: {push_result}"
                ) from push_result
    except TtsError:
        raise
    except CartesiaError as exc:
        raise TtsError(f"Cartesia TTS failed for call {call_id}: {exc}") from exc
    except (OSError, asyncio.TimeoutError) as exc:
        raise TtsError(f"Cartesia TTS unavailable for call {call_id}: {exc}") from exc
    finally:
        await client.close()
