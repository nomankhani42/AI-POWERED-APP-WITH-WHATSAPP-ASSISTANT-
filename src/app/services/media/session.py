"""Per-call media session orchestration: the automatic conversation loop (US4 + feature 004).

Each live call gets its own ``_CallSession`` keyed by ``call_id`` in the module-level
``_sessions`` registry, so concurrent calls stay fully isolated. When a call is attended the
session logs it, speaks a configurable welcome as the opening turn, then runs the
listen -> transcribe -> stream reply -> TTS loop turn-by-turn, logging each turn and
re-prompting / hanging up on caller silence, until the call ends.

Feature 004 behaviors layered on the 003 loop:
- **Streaming replies** (FR-007/008): the agent reply is streamed token-by-token via
  ``run_turn_stream`` and piped straight into ``synthesize_stream`` so playback starts on the
  first ready portion.
- **Barge-in** (FR-013): while a reply is playing, the first new caller segment stops playback
  immediately (``bridge.stop_playback``) and the loop switches back to listening.
- **Explicit failure handling** (FR-011): STT/TTS operations retry once silently; on continued
  failure the caller hears a brief spoken apology and the call continues (or ends gracefully
  rather than hanging on an unrecoverable transcription stream).
- **No-input** (FR-014): a silent caller is re-prompted once, then the call ends gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from app.agent.service import remember_assistant_message, run_turn_events
from app.core.config import get_settings
from app.services import meta_calling
from app.services.media.fillers import filler_for
from app.services.media.observability import (
    log_barge_in,
    log_call_attended,
    log_call_ended,
    log_fallback,
    log_filler,
    log_playback,
    log_reprompt,
    log_tool_call,
    log_tool_result,
    log_transcript,
    log_turn,
    log_welcome,
)
from app.services.media.types import ConversationTurn, TranscriptSegment
from app.services.media.webrtc import MediaBridge
from app.services.stt import SttError, transcribe_stream
from app.services.tts import DeepgramTtsSession, TtsError

logger = logging.getLogger(__name__)

# Spoken once when the caller has gone quiet, before hanging up on continued silence (FR-014).
_SILENCE_REPROMPT = "Are you still there?"
# Spoken when the agent returns nothing OR a provider (transcription/synthesis/reasoning) fails
# after a silent retry — the caller hears an apology and the call continues (FR-011).
_AGENT_FALLBACK = "Sorry, I didn't catch that. Could you say that again?"
# Spoken instead of sending a highly uncertain transcript to the booking agent.
_STT_CLARIFICATION = "Sorry, I didn't hear that clearly. Please say it again."


class _CallSession:
    """Owns one call's media bridge and its welcome + conversation loop task."""

    def __init__(self, call_id: str, caller: str, bridge: MediaBridge) -> None:
        self.call_id = call_id
        self.caller = caller
        self.bridge = bridge
        self._tts = DeepgramTtsSession(call_id)
        self._task: asyncio.Task[None] | None = None
        self._warm_task: asyncio.Task[None] | None = None
        self._turn = 0
        # Why the call ended, for the call_ended log record (FR-015). Updated as the loop
        # decides its exit path; defaults to a clean completion.
        self._end_reason = "completed"

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    def start_warming(self) -> None:
        """Begin the Deepgram handshake while SDP/ICE negotiation is in progress."""

        warm = getattr(self._tts, "warm", None)
        if warm is not None and self._warm_task is None:
            self._warm_task = asyncio.create_task(self._warm_tts(warm))

    async def _warm_tts(self, warm: object) -> None:
        try:
            await warm()  # type: ignore[operator]
        except asyncio.CancelledError:
            raise
        except Exception:
            # Synthesis still has its normal reconnect/retry path if warming fails.
            logger.warning(
                "media session: Deepgram TTS preconnect failed for call_id=%s",
                self.call_id,
                exc_info=True,
            )

    async def wait_ready(self) -> None:
        """Wait for WebRTC media and best-effort TTS preconnection."""

        await self.bridge.wait_connected(get_settings().media_connect_timeout_s)
        if self._warm_task is not None:
            await self._warm_task

    async def _run(self) -> None:
        try:
            # Call attended: record it before any audio (FR-015).
            log_call_attended(self.call_id, self.caller)
            # Opening turn: greet the caller automatically (FR-016).
            await self._play_welcome()
            # Then the turn-by-turn conversation loop.
            await self._conversation_loop()
        except asyncio.CancelledError:
            self._end_reason = "cancelled"
            raise
        except Exception:
            self._end_reason = "error"
            logger.exception("media session: unexpected error for call_id=%s", self.call_id)
        finally:
            # Full-timeline record that the call ended and why (FR-015 / SC-005).
            log_call_ended(self.call_id, self._end_reason)
            await self._tts.close()
            await self.bridge.close()
            _sessions.pop(self.call_id, None)

    async def _play_welcome(self) -> None:
        """Speak the configurable welcome message as turn 0 (FR-016/FR-022)."""

        text = get_settings().welcome_message
        if not text:
            return
        started = time.time()
        # Drain inbound RTP while the protected greeting plays. Merely delaying STT is not
        # enough: aiortc buffers received frames, so without this drain speech over the
        # welcome is transcribed as the caller's first turn after the greeting.
        async def _discard_welcome_audio() -> None:
            async for _chunk in self.bridge.inbound_pcm():
                pass

        discard_task = asyncio.create_task(_discard_welcome_audio())
        # Give the drain a chance to enter track.recv() before synthesis starts.
        await asyncio.sleep(0)
        log_welcome(self.call_id)
        try:
            if await self._speak(text):
                await self._remember_spoken(text)
        finally:
            discard_task.cancel()
            await asyncio.gather(discard_task, return_exceptions=True)
        log_turn(
            ConversationTurn(
                call_id=self.call_id,
                turn=0,
                transcript="",
                reply=text,
                started_at=started,
                ended_at=time.time(),
            )
        )

    async def _conversation_loop(self) -> None:
        """Listen, run a turn per finished utterance, handle silence and STT failures.

        The STT stream is (re)established per attempt: a transcription failure is retried
        silently up to ``provider_retry_attempts`` times; once exhausted the caller hears an
        apology and the call ends gracefully rather than hanging or looping (FR-011).
        """

        settings = get_settings()
        timeout = settings.caller_silence_timeout_s
        retries = settings.provider_retry_attempts
        reprompted = False
        stt_failures = 0

        while True:
            queue: asyncio.Queue[TranscriptSegment | None] = asyncio.Queue()
            error: list[BaseException] = []

            async def _pump() -> None:
                # Drain the STT stream into the queue so a silence timeout on ``queue.get``
                # never cancels an in-flight ``transcribe_stream``. Endpointing (~0.8 s) is
                # configured in stt.py, so ``is_final`` marks a real end-of-turn (FR-005).
                try:
                    async for segment in transcribe_stream(
                        self.bridge.inbound_pcm(), call_id=self.call_id
                    ):
                        # Log what STT heard as it arrives so transcription can be verified in
                        # the backend logs — finals at INFO, interims at DEBUG (FR-013).
                        if segment.text:
                            log_transcript(
                                self.call_id,
                                segment.text,
                                segment.is_final,
                                segment.confidence,
                            )
                        await queue.put(segment)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001 - surfaced below for retry
                    error.append(exc)
                finally:
                    await queue.put(None)

            pump = asyncio.create_task(_pump())
            try:
                while True:
                    try:
                        segment = await asyncio.wait_for(queue.get(), timeout=timeout)
                    except asyncio.TimeoutError:
                        if not reprompted:
                            reprompted = True
                            log_reprompt(self.call_id)
                            if await self._speak(_SILENCE_REPROMPT):
                                await self._remember_spoken(_SILENCE_REPROMPT)
                            continue
                        # Still silent after a re-prompt: end the call gracefully (FR-014).
                        self._end_reason = "caller_silence"
                        await self._terminate()
                        return
                    if segment is None:
                        break  # pump ended (stream closed or errored)
                    if segment.is_final and segment.text:
                        min_confidence = getattr(get_settings(), "stt_min_confidence", 0.5)
                        if (
                            segment.confidence is not None
                            and segment.confidence < min_confidence
                        ):
                            logger.info(
                                "media session: low-confidence STT "
                                "call_id=%s confidence=%.3f threshold=%.3f",
                                self.call_id,
                                segment.confidence,
                                min_confidence,
                            )
                            log_reprompt(self.call_id)
                            if await self._speak(_STT_CLARIFICATION):
                                await self._remember_spoken(_STT_CLARIFICATION)
                            reprompted = False
                            continue
                        reprompted = False
                        pending = await self._handle_turn(segment.text, queue)
                        # A caller who barged in with a finished utterance is handled at once.
                        while pending is not None and pending.is_final and pending.text:
                            pending = await self._handle_turn(pending.text, queue)
            finally:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)

            if not error:
                return  # transcription stream closed normally → call is over
            stt_failures += 1
            logger.warning(
                "media session: STT stream failed (attempt %d) for call_id=%s: %s",
                stt_failures,
                self.call_id,
                error[0],
            )
            if stt_failures <= retries:
                continue  # silent retry: re-establish the transcription stream
            # Retries exhausted: apologize once, then end gracefully — never hang or loop.
            log_fallback(self.call_id, self._turn)
            if await self._speak(_AGENT_FALLBACK):
                await self._remember_spoken(_AGENT_FALLBACK)
            self._end_reason = "stt_failure"
            await self._terminate()
            return

    async def _handle_turn(
        self, text: str, queue: asyncio.Queue[TranscriptSegment | None]
    ) -> TranscriptSegment | None:
        """Run one agent turn, then stream its text deltas directly into TTS.

        Tool events precede reply text in the agent contract, so fillers finish before the
        persistent Deepgram socket begins the answer. Once the first text delta arrives, the
        remaining deltas flow into Deepgram as they are generated; the TTS adapter coalesces
        them at sentence/clause boundaries for natural speech and low latency.
        """

        self._turn += 1
        turn_no = self._turn
        started = time.time()
        collected: list[str] = []
        events = run_turn_events(
            text, phone_number=self.caller, conversation_id=self.call_id, channel="voice"
        )
        event_iterator = events.__aiter__()
        first_text: str | None = None
        agent_ok = True

        try:
            while first_text is None:
                event = await anext(event_iterator)
                if event.kind == "tool_call":
                    log_tool_call(self.call_id, turn_no, event.tool_name)
                    await self._speak_filler(event.tool_name, turn_no)
                elif event.kind == "tool_output":
                    log_tool_result(self.call_id, turn_no, event.tool_name, bool(event.ok))
                elif event.kind == "text_delta" and event.text:
                    first_text = event.text
        except StopAsyncIteration:
            pass
        except Exception:
            agent_ok = False
            logger.exception("media session: agent turn failed for call_id=%s", self.call_id)

        async def _reply_chunks() -> AsyncIterator[str]:
            assert first_text is not None
            collected.append(first_text)
            yield first_text
            async for event in event_iterator:
                if event.kind == "text_delta" and event.text:
                    collected.append(event.text)
                    yield event.text
                elif event.kind == "tool_output":
                    log_tool_result(self.call_id, turn_no, event.tool_name, bool(event.ok))
                elif event.kind == "tool_call":
                    # The Agents SDK contract emits tools before answer text. Do not start a
                    # second playback while the reply owns the per-call TTS socket.
                    log_tool_call(self.call_id, turn_no, event.tool_name)
                    logger.warning(
                        "media session: late tool call after reply started call_id=%s tool=%s",
                        self.call_id,
                        event.tool_name,
                    )

        if agent_ok and first_text is not None:
            play_task = asyncio.create_task(self._play_reply(_reply_chunks(), turn_no))
            pending, played_ok = await self._play_with_barge_in(play_task, turn_no, queue)
        else:
            pending, played_ok = None, False

        reply = "".join(collected).strip()
        if not played_ok or not reply:
            log_fallback(self.call_id, turn_no)
            if await self._speak(_AGENT_FALLBACK):
                await self._remember_spoken(_AGENT_FALLBACK)
            if not reply:
                reply = _AGENT_FALLBACK
        log_turn(
            ConversationTurn(
                call_id=self.call_id,
                turn=turn_no,
                transcript=text,
                reply=reply,
                started_at=started,
                ended_at=time.time(),
            )
        )
        return pending

    async def _remember_spoken(self, text: str) -> None:
        """Best-effort write for prompts spoken outside the Agents SDK runner."""

        try:
            await remember_assistant_message(self.call_id, text)
        except Exception:
            logger.warning(
                "media session: failed to remember spoken assistant prompt for call_id=%s",
                self.call_id,
                exc_info=True,
            )

    async def _speak_filler(self, tool_name: str | None, turn_no: int) -> None:
        """Speak the tool-tailored filler for ``tool_name`` and log it (FR-006/FR-015)."""

        log_filler(self.call_id, turn_no, tool_name)
        await self._speak(filler_for(tool_name))

    async def _play_with_barge_in(
        self,
        play_task: asyncio.Task[bool],
        turn_no: int,
        queue: asyncio.Queue[TranscriptSegment | None],
    ) -> tuple[TranscriptSegment | None, bool]:
        """Await reply playback, optionally stopping when the caller speaks (FR-013).

        Returns ``(pending_segment, played_ok)``: ``pending_segment`` is a finished caller
        utterance that barged in (handle it next), and ``played_ok`` is False only when the
        reply genuinely failed to synthesize (a barge-in interruption is not a failure).
        """

        if not get_settings().barge_in_enabled:
            return None, await play_task

        stream_ended = False
        while not play_task.done():
            watch = asyncio.create_task(queue.get())
            done, _pending = await asyncio.wait(
                {play_task, watch}, return_when=asyncio.FIRST_COMPLETED
            )

            if play_task in done:
                played_ok = await self._task_bool(play_task)
                if watch.done() and not watch.cancelled():
                    segment = watch.result()
                    if self._is_finished_speech(segment):
                        return segment, played_ok
                    if segment is None:
                        stream_ended = True
                else:
                    watch.cancel()
                    await asyncio.gather(watch, return_exceptions=True)
                if stream_ended:
                    queue.put_nowait(None)
                return None, played_ok

            segment = watch.result()
            if segment is None:
                stream_ended = True
                continue
            if not self._is_speech(segment):
                # Empty/noise events are not caller barge-in; keep playing the assistant reply
                # to completion.
                continue

            # Caller barged in: stop playback and switch to listening.
            self.bridge.stop_playback()
            play_task.cancel()
            await asyncio.gather(play_task, return_exceptions=True)
            log_barge_in(self.call_id, turn_no)
            pending = segment if self._is_finished_speech(segment) else None
            return pending, True  # interruption is intentional, not a synthesis failure

        if stream_ended:
            queue.put_nowait(None)
        return None, await self._task_bool(play_task)

    @staticmethod
    def _is_speech(segment: TranscriptSegment | None) -> bool:
        return segment is not None and bool(segment.text.strip())

    @staticmethod
    def _is_finished_speech(segment: TranscriptSegment | None) -> bool:
        return _CallSession._is_speech(segment) and bool(segment.is_final)

    @staticmethod
    async def _task_bool(task: asyncio.Task[bool]) -> bool:
        """Result of a finished play task, treating cancellation/failure as 'not ok'."""

        await asyncio.gather(task, return_exceptions=True)
        if task.cancelled():
            return False
        exc = task.exception()
        return exc is None and bool(task.result())

    async def _play_reply(
        self, text: AsyncIterator[str] | str, turn_no: int
    ) -> bool:
        """Synthesize and play a complete reply. Return False if it failed."""

        try:
            log_playback(self.call_id, turn_no, "start")
            await self.bridge.play(self._tts.synthesize_stream(text))
            log_playback(self.call_id, turn_no, "stop")
            return True
        except asyncio.CancelledError:
            raise
        except TtsError:
            logger.exception("media session: TTS failed during reply for call_id=%s", self.call_id)
            return False
        except Exception:
            logger.exception("media session: agent turn failed for call_id=%s", self.call_id)
            return False

    async def _speak(self, text: str) -> bool:
        """Synthesize fixed ``text`` and play it, retrying once on TTS failure (FR-011).

        Used for the welcome, re-prompt, and apology — never recurses (an apology cannot
        apologize for itself); if every attempt fails it is logged and skipped.
        """

        if not text:
            return False
        attempts = 1 + get_settings().provider_retry_attempts
        for attempt in range(attempts):
            try:
                await self.bridge.play(self._tts.synthesize_stream(text))
                return True
            except TtsError:
                logger.warning(
                    "media session: TTS attempt %d/%d failed for call_id=%s",
                    attempt + 1,
                    attempts,
                    self.call_id,
                )
        logger.error(
            "media session: TTS gave up after %d attempts for call_id=%s", attempts, self.call_id
        )
        return False

    async def _terminate(self) -> None:
        """Ask Meta to end the call (best-effort); teardown follows in ``_run``'s finally."""

        try:
            await meta_calling.terminate(self.call_id)
        except Exception:
            logger.exception("media session: terminate failed for call_id=%s", self.call_id)

    async def stop(self) -> None:
        if self._warm_task is not None and not self._warm_task.done():
            self._warm_task.cancel()
            await asyncio.gather(self._warm_task, return_exceptions=True)
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.bridge.close()


# Registry of active sessions, keyed by call_id — the isolation boundary for concurrent calls.
_sessions: dict[str, _CallSession] = {}


def has_session(call_id: str) -> bool:
    """Return whether a prepared or running media session exists for ``call_id``."""

    return call_id in _sessions


async def prepare_session(call_id: str, caller: str, offer_sdp: str) -> str:
    """Negotiate and register media without starting welcome/conversation playback.

    Meta must accept the SDP answer before the caller can reliably receive media. Keeping
    preparation separate from activation prevents Deepgram TTS and the welcome from racing
    ahead of the Graph ``accept`` action.
    """

    if call_id in _sessions:
        raise RuntimeError(f"media session already exists for call {call_id}")

    bridge = MediaBridge(call_id)
    session = _CallSession(call_id, caller, bridge)
    session.start_warming()
    try:
        answer_sdp = await bridge.answer(offer_sdp)
    except BaseException:
        await session.stop()
        raise

    _sessions[call_id] = session
    return answer_sdp


def activate_session(call_id: str) -> bool:
    """Start a prepared session after Meta accepts it; return False if it was stopped."""

    session = _sessions.get(call_id)
    if session is None:
        return False
    session.start()
    return True


async def start_session(call_id: str, caller: str, offer_sdp: str) -> str:
    """Prepare and immediately activate media (used by tests and non-Meta callers)."""

    answer_sdp = await prepare_session(call_id, caller, offer_sdp)
    activate_session(call_id)
    return answer_sdp


async def wait_session_ready(call_id: str) -> bool:
    """Wait for transport/TTS readiness before starting welcome playback."""

    session = _sessions.get(call_id)
    if session is None:
        return False
    await session.wait_ready()
    return call_id in _sessions


async def stop_session(call_id: str) -> None:
    """Tear down the media session for ``call_id``, if one is active. Idempotent."""

    session = _sessions.pop(call_id, None)
    if session is not None:
        await session.stop()
