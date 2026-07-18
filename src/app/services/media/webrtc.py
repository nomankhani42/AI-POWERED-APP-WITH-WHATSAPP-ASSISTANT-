"""WebRTC media bridge (T017) — the ONLY module that touches ``aiortc``/``av`` directly.

Answers the caller's SDP offer, exposes inbound caller audio as linear16 PCM chunks
(suitable for ``stt.transcribe_stream``), and accepts outbound ``SpeechChunk`` PCM audio
to play back to the caller. Opus encode/decode is handled internally by aiortc's RTP
codec pipeline (``aiortc.codecs.opus``) — that codec resamples whatever PCM we hand it
to/from its own 48 kHz stereo wire format, so this module only ever deals in plain
linear16 PCM at ``settings.stt_sample_rate`` / the TTS output rate. See research.md §2.
"""

from __future__ import annotations

import asyncio
import fractions
import logging
import time
from collections.abc import AsyncIterator

import av
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError

from app.core.config import get_settings
from app.services.media.types import SpeechChunk

logger = logging.getLogger(__name__)

_BYTES_PER_SAMPLE = 2  # linear16 (s16) mono
_FRAME_MS = 20  # Opus packetization interval; the encoder wants fixed 20 ms frames.
_PLAYOUT_DRAIN_BUFFER_S = 0.35  # Let aiortc/RTP buffers finish before the session resumes.
# Start each reply this far behind real time. During this pre-roll ``recv`` is asleep while
# synthesis keeps enqueuing, so the frame queue builds a jitter buffer that absorbs later
# stalls (STT + LLM + TTS all share this event loop) without the queue draining — the cushion
# that stops choppy/stuttered and warbly "old TV" playback. 120 ms trades a little reply-onset
# latency for markedly steadier audio.
_PLAYOUT_PREROLL_S = 0.12


def _single_sha256_fingerprint(sdp: str) -> str:
    """Keep only the ``sha-256`` DTLS fingerprint, dropping aiortc's extra digests.

    aiortc >=1.x emits three ``a=fingerprint`` lines (sha-256, sha-384, sha-512) for the
    same certificate. That is valid per RFC 8122, but WhatsApp's Calling SDP validator
    rejects a multi-fingerprint answer as "SDP is invalid" (error 138008). All three are
    digests of the same cert, so keeping only sha-256 is safe for the DTLS handshake.
    Split/join on CRLF so RFC 4566 line endings are preserved (WhatsApp requires strict
    CRLF).
    """

    kept: list[str] = []
    seen_sha256 = False
    for line in sdp.split("\r\n"):
        if line.startswith("a=fingerprint:"):
            if line.startswith("a=fingerprint:sha-256 ") and not seen_sha256:
                seen_sha256 = True
                kept.append(line)
            continue
        kept.append(line)
    return "\r\n".join(kept)

# Public STUN so aiortc can gather server-reflexive candidates — otherwise, behind NAT,
# the answer carries only unreachable private host candidates.
_ICE_SERVERS = [RTCIceServer(urls="stun:stun.l.google.com:19302")]


def _packed_mono_pcm(frame: av.AudioFrame) -> bytes:
    """Return active s16 mono samples without PyAV's alignment padding."""

    return bytes(frame.planes[0])[: frame.samples * _BYTES_PER_SAMPLE]


class _OutboundTrack(MediaStreamTrack):
    """Custom audio track that repacketizes synthesized PCM into fixed 20 ms frames.

    The garbled "old TV / toun toush" playback came from handing aiortc's Opus encoder one
    ``av.AudioFrame`` per Cartesia chunk of *arbitrary* length: the encoder wants fixed 20 ms
    frames at a single rate, so irregular frames were resampled/re-sliced at the encode seam,
    producing periodic clicking and stutter (research.md §1). This track instead buffers
    incoming PCM (resampling to ``rate`` if a chunk arrives at a different rate) and emits
    **only** exact ``frame_samples``-length frames with monotonic ``pts``. aiortc calls
    ``recv()`` in a loop; the track itself is responsible for real-time pacing — sleeping
    until wall-clock time catches up with the cumulative sample timestamp.
    """

    kind = "audio"

    def __init__(self, rate: int) -> None:
        super().__init__()
        self._rate = rate
        self._frame_samples = rate * _FRAME_MS // 1000  # 960 @ 48 kHz
        self._frame_bytes = self._frame_samples * _BYTES_PER_SAMPLE  # 1920 bytes
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        self._buffer = bytearray()  # sub-frame remainder carried between chunks
        self._frames: asyncio.Queue[bytes] = asyncio.Queue()  # full 20 ms frames, ready to send
        self._in_pts = 0
        self._timestamp = 0
        self._start: float | None = None
        self._anchor_pending = False
        self._last_frame_end_at: float | None = None
        self._stopped = False

    def begin_playback(self) -> None:
        """Start a new spoken reply without inheriting stale wall-clock pacing.

        ``pts`` stays monotonic for the lifetime of the WebRTC track, but after a quiet gap the
        old clock would put the next frames in the past and make aiortc pull them in a burst.
        The clock is re-anchored on the *first frame actually played* (in ``recv``), not here:
        ``begin_playback`` runs before the first synthesized chunk arrives (TTS handshake +
        first-audio latency), so anchoring now would leave ``recv`` "behind" and burst-deliver a
        backlog to the encoder — the rushed, choppy onset at the start of every reply. Deferring
        also lets a small pre-roll of frames accumulate to absorb event-loop jitter.
        """

        self._stopped = False
        self._last_frame_end_at = None
        # Anything still queued belongs to a previous reply (e.g. a chunk pushed in the race
        # between a barge-in flush and its playback task being cancelled) — never play it
        # at the start of this reply.
        self._discard_pending()
        self._anchor_pending = True

    def push(self, pcm: bytes, sample_rate: int) -> None:
        """Buffer synthesized PCM, slicing out every complete 20 ms frame as it fills."""

        self._stopped = False
        self._buffer.extend(self._to_output_rate(pcm, sample_rate))
        while len(self._buffer) >= self._frame_bytes:
            frame = bytes(self._buffer[: self._frame_bytes])
            del self._buffer[: self._frame_bytes]
            self._frames.put_nowait(frame)

    def flush_tail(self) -> None:
        """Emit any sub-frame remainder as a final zero-padded frame (end of a reply)."""

        if self._buffer:
            frame = bytes(self._buffer) + b"\x00" * (self._frame_bytes - len(self._buffer))
            self._buffer.clear()
            self._frames.put_nowait(frame)

    def flush(self) -> None:
        """Discard all pending audio so the current reply stops (barge-in). Idempotent."""

        self._stopped = True
        self._last_frame_end_at = None
        self._discard_pending()

    def _discard_pending(self) -> None:
        """Drop buffered PCM and queued frames, keeping the queue's join() bookkeeping."""

        self._buffer.clear()
        while not self._frames.empty():
            try:
                self._frames.get_nowait()
                self._frames.task_done()
            except asyncio.QueueEmpty:  # pragma: no cover - loop guard
                break

    async def drain(self) -> None:
        """Wait until queued frames should have finished playing on the wire."""

        await self._frames.join()
        if self._stopped or self._last_frame_end_at is None:
            return
        wait = self._last_frame_end_at + _PLAYOUT_DRAIN_BUFFER_S - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    def _to_output_rate(self, pcm: bytes, sample_rate: int) -> bytes:
        """Return ``pcm`` at the outbound rate; passthrough when it already matches."""

        if sample_rate == self._rate:
            return pcm
        frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm) // _BYTES_PER_SAMPLE)
        frame.planes[0].update(pcm)
        frame.sample_rate = sample_rate
        frame.pts = self._in_pts
        frame.time_base = fractions.Fraction(1, sample_rate)
        self._in_pts += frame.samples
        out = bytearray()
        for resampled in self._resampler.resample(frame):
            # PyAV planes may be alignment-padded. Only forward the active PCM samples;
            # padding becomes audible silence/clicks and changes stream timing.
            out.extend(_packed_mono_pcm(resampled))
        return bytes(out)

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError("track not live")

        pcm = await self._frames.get()
        samples = len(pcm) // _BYTES_PER_SAMPLE

        frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
        frame.planes[0].update(pcm)
        frame.sample_rate = self._rate
        frame.time_base = fractions.Fraction(1, self._rate)

        if self._start is None or self._anchor_pending:
            # Anchor the playout clock to this first frame plus a small pre-roll, so the reply
            # starts slightly behind real time (jitter cushion) instead of bursting to catch up
            # a clock set before any audio existed. Monotonic clock: immune to NTP/suspend jumps.
            self._anchor_pending = False
            self._start = time.monotonic() + _PLAYOUT_PREROLL_S - (self._timestamp / self._rate)
        now = time.monotonic()
        wait = self._start + (self._timestamp / self._rate) - now
        if wait > 0:
            await asyncio.sleep(wait)
        elif wait < -_PLAYOUT_PREROLL_S:
            # Underrun: synthesis (LLM tokens → TTS) fell behind real time and the pre-roll
            # cushion is spent — the frame queue drained and this frame arrived late. Re-anchor
            # so it plays NOW instead of bursting the whole backlog into the Opus encoder, which
            # is the warbly "old TV" distortion. Playback simply tracks synthesis from here; the
            # cushion rebuilds once synthesis runs ahead again. A tiny gap is far less audible
            # than garbled speed-up.
            self._start = now - (self._timestamp / self._rate)
        frame.pts = self._timestamp
        self._last_frame_end_at = self._start + ((self._timestamp + samples) / self._rate)
        self._timestamp += samples
        self._frames.task_done()
        return frame


class MediaBridge:
    """Per-call aiortc peer connection bridging caller audio <-> our PCM pipeline."""

    def __init__(self, call_id: str) -> None:
        self._call_id = call_id
        self._pc = RTCPeerConnection(RTCConfiguration(iceServers=_ICE_SERVERS))
        self._outbound = _OutboundTrack(get_settings().tts_output_sample_rate)
        self._pc.addTrack(self._outbound)
        self._inbound_track: MediaStreamTrack | None = None
        self._logged_first_inbound = False
        self._closed = False
        self._state_changed = asyncio.Event()

        @self._pc.on("track")
        def _on_track(track: MediaStreamTrack) -> None:
            if track.kind == "audio":
                self._inbound_track = track
                logger.info("MediaBridge[%s] inbound audio track attached", self._call_id)

        @self._pc.on("connectionstatechange")
        def _on_connection_state_change() -> None:
            logger.info(
                "MediaBridge[%s] connection_state=%s ice_state=%s",
                self._call_id,
                self._pc.connectionState,
                self._pc.iceConnectionState,
            )
            self._state_changed.set()

    async def answer(self, offer_sdp: str) -> str:
        """Apply the caller's SDP offer and return our SDP answer."""

        logger.info("MediaBridge[%s] OFFER SDP:\n%s", self._call_id, offer_sdp)
        await self._pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)
        assert self._pc.localDescription is not None
        answer_sdp = _single_sha256_fingerprint(self._pc.localDescription.sdp)
        logger.info("MediaBridge[%s] ANSWER SDP:\n%s", self._call_id, answer_sdp)
        return answer_sdp

    async def wait_connected(self, timeout_s: float) -> None:
        """Wait until ICE and DTLS are ready before allowing outbound playback."""

        async def _wait() -> None:
            while self._pc.connectionState != "connected":
                state = self._pc.connectionState
                if self._closed or state in {"failed", "closed"}:
                    raise ConnectionError(
                        f"WebRTC transport {state} for call {self._call_id}"
                    )
                self._state_changed.clear()
                if self._pc.connectionState == "connected":
                    return
                await self._state_changed.wait()

        try:
            async with asyncio.timeout(timeout_s):
                await _wait()
        except TimeoutError as exc:
            raise TimeoutError(
                f"WebRTC transport did not connect for call {self._call_id} "
                f"within {timeout_s:g}s"
            ) from exc

    async def inbound_pcm(self) -> AsyncIterator[bytes]:
        """Yield decoded caller audio as linear16 PCM chunks at ``stt_sample_rate``."""

        rate = get_settings().stt_sample_rate
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)

        waited = 0.0
        warned_no_track = False
        while not self._closed:
            track = self._inbound_track
            if track is None:
                # No caller audio track yet. If it never attaches, caller audio can't reach STT
                # ("not listening") — surface it once instead of looping silently forever.
                await asyncio.sleep(0.02)
                waited += 0.02
                if not warned_no_track and waited >= 2.0:
                    warned_no_track = True
                    logger.warning(
                        "MediaBridge[%s] still no inbound audio track after %.0fs — caller "
                        "audio is not reaching STT (offer missing a sendrecv audio m-line?)",
                        self._call_id,
                        waited,
                    )
                continue
            try:
                frame = await track.recv()
            except MediaStreamError:
                logger.info("MediaBridge[%s] inbound audio track ended", self._call_id)
                return
            for out_frame in resampler.resample(frame):
                # Do not send PyAV's alignment padding to Deepgram as if it were captured
                # audio. It skews real-time pacing and can degrade endpointing/STT accuracy.
                data = _packed_mono_pcm(out_frame)
                if data:
                    if not self._logged_first_inbound:
                        self._logged_first_inbound = True
                        logger.info(
                            "MediaBridge[%s] first inbound caller audio received (%d Hz)",
                            self._call_id,
                            rate,
                        )
                    yield data

    async def play(self, chunks: AsyncIterator[SpeechChunk]) -> None:
        """Enqueue synthesized speech chunks onto the outbound track for playback.

        After the reply's chunks are exhausted, flush the sub-frame remainder as a final
        zero-padded frame so replies stay self-contained and no tail leaks into the next one.
        """

        self._outbound.begin_playback()
        async for chunk in chunks:
            self._outbound.push(chunk.audio, chunk.sample_rate)
        self._outbound.flush_tail()
        await self._outbound.drain()

    def stop_playback(self) -> None:
        """Immediately drop any queued/buffered playback (barge-in). Idempotent."""

        self._outbound.flush()

    async def close(self) -> None:
        """Tear down the peer connection. Safe to call more than once."""

        if self._closed:
            return
        self._closed = True
        await self._pc.close()
