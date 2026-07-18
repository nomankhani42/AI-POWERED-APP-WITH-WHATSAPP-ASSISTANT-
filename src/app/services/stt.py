"""Speech-to-text service: Deepgram streaming (chunked) STT.

Wraps Deepgram's real-time streaming websocket behind a single ``transcribe_stream()``
async generator so the provider stays swappable (constitution Principle IV) and the
capability is testable in isolation from the webhook/media layer (FR-007, FR-010).

ASSUMPTION / deviation from ``contracts/stt-service.md``: the contract sketches
``listen.v2.connect(...)`` + ``send_finalize()``. The installed SDK is
``deepgram-sdk==7.4.0``; its real ``listen.v2`` ("Flux") client only accepts
``model in {"flux-general-en", "flux-general-multi"}`` and its socket client has **no**
``send_finalize()`` (only ``send_media`` / ``send_close_stream`` / ``send_configure``).
``settings.deepgram_model`` defaults to ``"nova-3"`` — a classic model, not a Flux model.
``listen.v1.connect(...)`` is the API that (a) accepts ``nova-3``, (b) exposes
``send_finalize()`` + ``send_close_stream()`` exactly as the contract describes, and
(c) reports utterance end via the ``speech_final`` flag on ``Results`` messages. We
therefore stream through ``client.listen.v1.connect(...)``, which delivers the same
"chunked streaming with server-side endpointing" behavior the contract and
research.md §3 call for. Swapping to v2/Flux later is confined to the ``connect(...)``
call below.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

from app.core.config import get_settings
from app.services.media.types import TranscriptSegment

logger = logging.getLogger(__name__)

# Deepgram closes a Listen socket with NET-0001 after ~10 s without audio. WhatsApp's Opus
# stream uses DTX, so a silent caller stops producing frames entirely — without KeepAlives
# the STT stream dies mid-call and silence gets misreported as a transcription failure.
_KEEPALIVE_INTERVAL_S = 5.0

# After the caller stops speaking, DTX suppresses RTP so Deepgram never receives the trailing
# silence its endpointing (``speech_final``) needs, and ``UtteranceEnd`` can't fire either since
# the audio timeline stops advancing — the turn would hang and the agent never replies. Once
# audio has been idle this long with a turn in progress we send a ``Finalize`` to force Deepgram
# to close out the utterance; it answers with a result carrying ``from_finalize`` (see
# ``_on_message``). Kept above the ~0.8 s endpointing so real endpointing wins when it can.
_ENDPOINT_IDLE_FINALIZE_S = 1.2


class SttError(Exception):
    """Raised when the Deepgram STT provider fails, times out, or errors mid-stream (FR-009)."""


@dataclass(slots=True)
class _Done:
    """Sentinel: the Deepgram socket closed; no more segments will arrive."""


@dataclass(slots=True)
class _Failed:
    """Sentinel: the stream (connection, send, or receive) failed."""

    error: BaseException


async def transcribe_stream(
    audio_chunks: AsyncIterator[bytes],
    *,
    call_id: str,
    sample_rate: int | None = None,
) -> AsyncIterator[TranscriptSegment]:
    """Stream linear16 PCM chunks to Deepgram and yield transcript segments as they arrive.

    Interim segments (``is_final=False``) surface partial text as Deepgram refines it; a
    segment flips to ``is_final=True`` when Deepgram's endpointing marks the caller's
    utterance complete (``speech_final``) — that is what should drive one agent turn.
    Silence yields zero segments rather than an error (FR-011). Any connection, send, or
    receive failure — including timeouts — raises ``SttError`` (FR-009); it never hangs.
    """

    settings = get_settings()
    rate = sample_rate or settings.stt_sample_rate
    client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)
    queue: asyncio.Queue[TranscriptSegment | _Done | _Failed] = asyncio.Queue()

    # Finalized (``is_final``) segment texts accumulated for the current caller turn. Deepgram
    # can split one utterance into several ``is_final`` segments, so we join them and only emit
    # a FINAL end-of-turn transcript when Deepgram signals the utterance is complete — via
    # ``speech_final`` (endpointing) or, as a backstop when that doesn't fire (noisy line, no
    # clear pause), an ``UtteranceEnd`` message (enabled by utterance_end_ms/vad_events).
    turn_segments: list[str] = []
    turn_confidences: list[float] = []

    def _flush_turn() -> None:
        """Emit the accumulated turn as one FINAL segment and reset the buffer."""

        full = " ".join(s for s in turn_segments if s).strip()
        turn_segments.clear()
        confidence = min(turn_confidences) if turn_confidences else None
        turn_confidences.clear()
        if full:
            queue.put_nowait(
                TranscriptSegment(
                    call_id=call_id,
                    text=full,
                    is_final=True,
                    ts=time.time(),
                    confidence=confidence,
                )
            )

    def _on_message(message: Any) -> None:
        # UtteranceEnd is the endpointing backstop: end the turn even if ``speech_final`` never
        # arrived. It carries no transcript, so handle it before pulling text.
        if getattr(message, "type", None) == "UtteranceEnd":
            _flush_turn()
            return

        text, confidence = _extract_result(message)
        # End the turn on ``speech_final`` (Deepgram's silence endpointing) OR ``from_finalize``
        # (Deepgram's reply to the Finalize we send when DTX stalls the audio — see _send_all).
        # Both mark the real end of the caller's turn (FR-005/FR-012). Deepgram's plain
        # ``is_final`` only locks in a partial segment mid-utterance and fires repeatedly while
        # the caller is still talking, so it must NOT end a turn on its own — otherwise the agent
        # jumps in early and the caller gets cut off / re-prompted mid-sentence.
        if bool(getattr(message, "speech_final", False)) or bool(
            getattr(message, "from_finalize", False)
        ):
            if text:
                turn_segments.append(text)
                if confidence is not None:
                    turn_confidences.append(confidence)
            _flush_turn()
            return
        if not text:
            return
        if bool(getattr(message, "is_final", False)):
            # Segment finalized but the utterance continues: keep it and surface cumulative
            # progress as an interim (barge-in signal + live transcript), don't end the turn.
            turn_segments.append(text)
            if confidence is not None:
                turn_confidences.append(confidence)
            queue.put_nowait(
                TranscriptSegment(
                    call_id=call_id,
                    text=" ".join(turn_segments).strip(),
                    is_final=False,
                    ts=time.time(),
                    confidence=confidence,
                )
            )
        else:
            # Interim partial: cumulative text so far, not final (barge-in / live view).
            partial = " ".join([*turn_segments, text]).strip()
            queue.put_nowait(
                TranscriptSegment(
                    call_id=call_id,
                    text=partial,
                    is_final=False,
                    ts=time.time(),
                    confidence=confidence,
                )
            )

    def _on_open(_: Any) -> None:
        logger.info(
            "Deepgram STT connected (model=%s, rate=%d Hz) for call %s",
            settings.deepgram_model,
            rate,
            call_id,
        )

    def _on_error(exc: Any) -> None:
        logger.warning("Deepgram STT error for call %s: %s", call_id, exc)
        queue.put_nowait(_Failed(exc if isinstance(exc, BaseException) else SttError(str(exc))))

    def _on_close(_: Any) -> None:
        queue.put_nowait(_Done())

    try:
        async with client.listen.v1.connect(
            model=settings.deepgram_model,
            encoding="linear16",
            sample_rate=rate,
            channels=1,
            language=settings.deepgram_language,
            keyterm=[
                term.strip()
                for term in settings.deepgram_keyterms.split(",")
                if term.strip()
            ],
            smart_format=settings.stt_smart_format,
            # Silence-based endpointing: a caller turn finalizes (``speech_final``) only after
            # ~0.8 s of silence, so a brief mid-sentence pause does not cut the caller off
            # (FR-005/FR-012, research.md §3). ``interim_results`` must stay on for both the
            # interim barge-in signal and the ``UtteranceEnd`` backstop.
            interim_results=True,
            endpointing=settings.stt_endpointing_ms,
            utterance_end_ms=str(settings.stt_utterance_end_ms),
            vad_events=True,
        ) as connection:
            connection.on(EventType.OPEN, _on_open)
            connection.on(EventType.MESSAGE, _on_message)
            connection.on(EventType.ERROR, _on_error)
            connection.on(EventType.CLOSE, _on_close)

            listen_task = asyncio.create_task(connection.start_listening())
            send_task = asyncio.create_task(_send_all(connection, audio_chunks, queue))
            try:
                while True:
                    event = await queue.get()
                    if isinstance(event, _Failed):
                        raise SttError(
                            f"Deepgram STT stream failed for call {call_id}: {event.error}"
                        ) from event.error
                    if isinstance(event, _Done):
                        break
                    yield event
            finally:
                for task in (send_task, listen_task):
                    task.cancel()
                await asyncio.gather(send_task, listen_task, return_exceptions=True)
    except SttError:
        raise
    except Exception as exc:  # connection refused, auth failure, timeout, protocol error
        raise SttError(f"Deepgram STT connection failed for call {call_id}: {exc}") from exc


async def _send_all(
    connection: Any, audio_chunks: AsyncIterator[bytes], queue: asyncio.Queue[Any]
) -> None:
    """Forward inbound audio chunks, then flush and close the turn per the contract.

    When no chunk arrives for ``_KEEPALIVE_INTERVAL_S`` a ``KeepAlive`` message is sent
    instead, so caller silence (or DTX suppressing RTP frames) never lets Deepgram close
    the socket with NET-0001. The pending ``__anext__`` is never cancelled on that path —
    cancelling it would kill the inbound audio generator itself.
    """

    iterator = audio_chunks.__aiter__()
    next_task: asyncio.Task[bytes] | None = None
    idle_elapsed = 0.0  # seconds since the last inbound audio chunk (reset on each chunk)
    turn_pending = False  # audio has been sent that Deepgram has not yet been asked to finalize
    try:
        while True:
            if next_task is None:
                next_task = asyncio.ensure_future(iterator.__anext__())
            # Wake at whichever comes first: the finalize backstop (only while a turn is in
            # progress) or the keepalive interval.
            wait_s = _KEEPALIVE_INTERVAL_S
            if turn_pending:
                wait_s = min(wait_s, max(0.0, _ENDPOINT_IDLE_FINALIZE_S - idle_elapsed))
            started = time.monotonic()
            done, _pending = await asyncio.wait({next_task}, timeout=wait_s)
            if not done:
                idle_elapsed += time.monotonic() - started
                if turn_pending and idle_elapsed >= _ENDPOINT_IDLE_FINALIZE_S:
                    # Caller paused and DTX stopped the RTP stream: force Deepgram to finalize
                    # the pending utterance so the turn completes instead of hanging until the
                    # caller happens to speak again.
                    await connection.send_finalize()
                    turn_pending = False
                await connection.send_keep_alive()
                continue
            idle_elapsed = 0.0
            finished, next_task = next_task, None
            try:
                chunk = finished.result()
            except StopAsyncIteration:
                break
            await connection.send_media(chunk)
            turn_pending = True
        await connection.send_finalize()
        await connection.send_close_stream()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        queue.put_nowait(_Failed(exc))
    finally:
        if next_task is not None:
            next_task.cancel()
            await asyncio.gather(next_task, return_exceptions=True)


def _extract_result(message: Any) -> tuple[str, float | None]:
    """Pull transcript and a representative confidence from the best Deepgram alternative."""

    channel = getattr(message, "channel", None)
    alternatives = getattr(channel, "alternatives", None) if channel is not None else None
    if not alternatives:
        return "", None

    alternative = alternatives[0]
    text = (getattr(alternative, "transcript", "") or "").strip()
    word_confidences = [
        float(confidence)
        for word in (getattr(alternative, "words", None) or [])
        if (confidence := getattr(word, "confidence", None)) is not None
    ]
    if word_confidences:
        # Average across words rather than take the minimum. A single inherently-uncertain
        # token — a proper name ("Khan"), a room number ("502") — legitimately scores low and
        # must not drag a clear sentence below the clarification threshold; the old min() did
        # exactly that and trapped callers in an endless "I didn't hear that" re-prompt loop.
        # The booking agent confirms uncertain slot values itself, so a representative overall
        # score is the right signal for "was any of this intelligible?".
        return text, sum(word_confidences) / len(word_confidences)
    confidence = getattr(alternative, "confidence", None)
    return text, float(confidence) if confidence is not None else None
