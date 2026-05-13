"""WhatsApp Business Calling service.

Handles live WebRTC voice calls delivered via the WhatsApp Cloud API.

Meta flow (USER_INITIATED) per
https://developers.facebook.com/docs/whatsapp/cloud-api/calling :

  1. Webhook  field="calls"  event="connect"     → carries SDP offer
  2. Server   POST /{phone_number_id}/calls      action="pre_accept" + SDP answer
  3. Server   waits until ICE/WebRTC is connected
  4. Server   POST /{phone_number_id}/calls      action="accept"     + SDP answer
  5. Audio flows over the negotiated WebRTC path
  6. Webhook  event="terminate"                  → cleanup

If `accept` is sent before `pre_accept`, Meta rejects the call.

Active call sessions are stored in _ACTIVE_CALLS keyed by call_id.
"""

from __future__ import annotations

import asyncio
import fractions
import math
import re
import traceback
from typing import Optional

# Sentence boundary used to flush streaming agent text into TTS-sized
# utterances. Matches terminal punctuation followed by whitespace, or
# any newline. Tiny fragments are held back via _MIN_SENTENCE_CHARS so
# we don't ship one-word TTS calls.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[\.!\?…。！？])\s+|\n+")
_MIN_SENTENCE_CHARS = 18


def _split_off_sentences(buffer: str) -> tuple[list[str], str]:
    """Pull complete sentences off the front of a streaming text buffer.

    A sentence is committed only when it ends in terminal punctuation
    followed by whitespace/newline AND is at least ``_MIN_SENTENCE_CHARS``
    long. Shorter fragments stay buffered so we don't ship one-word
    TTS calls (e.g. a leading "Sure.").
    """
    sentences: list[str] = []
    remainder = buffer
    while True:
        match = _SENTENCE_BOUNDARY.search(remainder)
        if not match:
            break
        head = remainder[: match.end()].strip()
        tail = remainder[match.end():]
        if len(head) < _MIN_SENTENCE_CHARS:
            break
        sentences.append(head)
        remainder = tail
    return sentences, remainder

import httpx
import numpy as np

from agents import RunHooks

from config import (
    TURN_CREDENTIAL,
    TURN_URL,
    TURN_USERNAME,
    WEBRTC_UDP_PORT_RANGE,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_BASE,
    WHATSAPP_PHONE_NUMBER_ID,
)

try:
    import av
    from aiortc import (
        MediaStreamTrack,
        RTCConfiguration,
        RTCIceServer,
        RTCPeerConnection,
        RTCSessionDescription,
    )
    _AIORTC_AVAILABLE = True
except ImportError:
    _AIORTC_AVAILABLE = False
    print(">>> [Calling] aiortc not installed — voice calls will not be answered")


def _build_ice_config() -> Optional["RTCConfiguration"]:
    """Build the ICE configuration: STUN for candidate discovery, TURN for relay.

    Without a TURN server, a server behind symmetric NAT will fail to receive
    audio from Meta (no UDP path can be opened). Set TURN_URL in .env to fix.
    """
    if not _AIORTC_AVAILABLE:
        return None

    servers: list["RTCIceServer"] = [
        RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
        RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
    ]
    if TURN_URL:
        servers.append(
            RTCIceServer(
                urls=[TURN_URL],
                username=TURN_USERNAME or None,
                credential=TURN_CREDENTIAL or None,
            )
        )
        print(f">>> [Calling] TURN relay configured: {TURN_URL}")
    else:
        print(">>> [Calling] WARNING: no TURN configured — calls may fail behind NAT")

    return RTCConfiguration(iceServers=servers)


_ICE_CONFIG = _build_ice_config()


def _apply_udp_port_range() -> None:
    """Pin aiortc's UDP port range so firewall rules can be deterministic."""
    if not WEBRTC_UDP_PORT_RANGE:
        return
    try:
        low_str, high_str = WEBRTC_UDP_PORT_RANGE.split("-", 1)
        low, high = int(low_str), int(high_str)
    except ValueError:
        print(f">>> [Calling] Bad WEBRTC_UDP_PORT_RANGE {WEBRTC_UDP_PORT_RANGE!r} — ignoring")
        return

    try:
        # aioice (used by aiortc) reads these module globals when choosing ports.
        import aioice.ice as _ice
        _ice.UDP_TRANSPORT_PORT_RANGE = (low, high)
        print(f">>> [Calling] aiortc UDP port range pinned to {low}-{high}")
    except Exception as exc:
        print(f">>> [Calling] Could not pin UDP port range: {exc}")


if _AIORTC_AVAILABLE:
    _apply_udp_port_range()

# ── active sessions: call_id → CallSession ────────────────────────────
_ACTIVE_CALLS: dict[str, "CallSession"] = {}

# ── greeting cache ────────────────────────────────────────────────────
# English-only greeting (Urdu temporarily disabled while STT is locked to
# English in part2_stt.py — a multilingual greeting invites callers to
# answer in Urdu, which the current STT pipeline can't transcribe).
_GREETING_TEXT = (
    "Hello, welcome to The Grand Dine. "
    "How may I help you today?"
)
_greeting_pcm_cache: Optional[bytes] = None
_greeting_lock: Optional[asyncio.Lock] = None


async def _get_greeting_pcm() -> bytes:
    """Return the greeting PCM, synthesising on first call and caching after.

    Subsequent calls return instantly. The lock ensures we don't make two
    parallel TTS requests when two calls arrive at once before the first cache.
    """
    global _greeting_pcm_cache, _greeting_lock
    if _greeting_pcm_cache is not None:
        return _greeting_pcm_cache
    if _greeting_lock is None:
        _greeting_lock = asyncio.Lock()
    async with _greeting_lock:
        if _greeting_pcm_cache is None:
            from services.ai_services.tts_openai import synthesise_to_bytes
            print(">>> [Calling] Synthesising greeting (one-time)")
            _greeting_pcm_cache = await synthesise_to_bytes(_GREETING_TEXT)
            print(f">>> [Calling] Greeting cached: {len(_greeting_pcm_cache)} bytes")
    return _greeting_pcm_cache

# ── audio constants ───────────────────────────────────────────────────
_SAMPLE_RATE = 24_000          # 24 kHz — matches Groq STT and OpenAI TTS (incoming side)
_OUT_RATE = 48_000             # 48 kHz — Opus RTP clock (outgoing side)
_OUT_FRAME_SAMPLES = 960       # 20 ms @ 48 kHz — standard Opus frame size
_OUT_FRAME_BYTES = _OUT_FRAME_SAMPLES * 2  # int16 mono
_CHANNELS = 1                  # mono
_SILENCE_RMS = 400.0           # RMS below this = silence (phone audio noise floor ~50–200)
_SILENCE_FRAMES = 25           # ~0.5 s silence (at 50 fps) triggers end-of-utterance
_MIN_SPEECH_FRAMES = 10        # need at least this many speech frames to bother with STT
_MAX_BUFFER_SECS = 10          # flush buffer after this many seconds anyway
_RMS_LOG_INTERVAL = 50         # log mic levels every N frames (~1 s)
_MAX_PROCESSING_SECS = 45.0    # force-clear a stuck _processing flag after this long
_TTS_TIMEOUT_SECS = 15.0       # per-sentence TTS hard timeout (OpenAI default is 10 min)
_AGENT_STREAM_TIMEOUT_SECS = 60.0  # end-to-end ceiling on agent stream + TTS feeding

# ── STT prompt & transcript normalisation ────────────────────────────

# Spoken ordinals + cardinals (1-31) for date normalisation.
_SPOKEN_TO_DIGIT: dict[str, str] = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
    "fifteenth": "15", "sixteenth": "16", "seventeenth": "17", "eighteenth": "18",
    "nineteenth": "19", "twentieth": "20", "twenty first": "21",
    "twenty second": "22", "twenty third": "23", "twenty fourth": "24",
    "twenty fifth": "25", "twenty sixth": "26", "twenty seventh": "27",
    "twenty eighth": "28", "twenty ninth": "29", "thirtieth": "30",
    "thirty first": "31",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
}
_MONTHS_PATTERN = (
    "january|february|march|april|may|june|july|august"
    "|september|october|november|december"
)


def _normalise_transcript(text: str) -> str:
    """Convert spoken date patterns to digit+month form.

    Handles all common spoken orderings:
      "sixth june"          → "6 June"
      "the sixth of june"   → "6 June"
      "june the sixth"      → "6 June"
      "june sixth"          → "6 June"
      "six june"            → "6 June"   (cardinal)
    """
    t = text
    for spoken, digit in _SPOKEN_TO_DIGIT.items():
        spoken_re = re.escape(spoken)
        # "<spoken> [of] <month>"  and  "the <spoken> [of] <month>"
        t = re.sub(
            rf'\bthe\s+{spoken_re}(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTHS_PATTERN})\b',
            lambda m, d=digit: f'{d} {m.group(1).capitalize()}',
            t, flags=re.IGNORECASE,
        )
        t = re.sub(
            rf'\b{spoken_re}(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTHS_PATTERN})\b',
            lambda m, d=digit: f'{d} {m.group(1).capitalize()}',
            t, flags=re.IGNORECASE,
        )
        # "<month> [the] <spoken>"
        t = re.sub(
            rf'\b({_MONTHS_PATTERN})\s+(?:the\s+)?{spoken_re}(?:st|nd|rd|th)?\b',
            lambda m, d=digit: f'{d} {m.group(1).capitalize()}',
            t, flags=re.IGNORECASE,
        )
    return t


def _resample_pcm_24_to_48(pcm_24: bytes) -> bytes:
    """Upsample 24 kHz int16 mono PCM → 48 kHz int16 mono PCM via PyAV."""
    if not pcm_24:
        return b""
    in_frame = av.AudioFrame(format="s16", layout="mono", samples=len(pcm_24) // 2)
    in_frame.planes[0].update(pcm_24)
    in_frame.sample_rate = _SAMPLE_RATE
    in_frame.pts = None
    resampler = av.AudioResampler(format="s16", layout="mono", rate=_OUT_RATE)
    out_frames = resampler.resample(in_frame)
    return b"".join(bytes(f.planes[0]) for f in out_frames)


# ─────────────────────────────────────────────────────────────────────
# Custom audio track — feeds TTS PCM back to the caller
# ─────────────────────────────────────────────────────────────────────

class _TtsAudioTrack(MediaStreamTrack):
    """Real-time-paced audio track. Outputs Opus-friendly 48 kHz frames.

    Two reasons this exists rather than something simpler:
      1. aiortc's sender calls recv() in a tight loop and trusts the track to
         pace itself to wall-clock. Without sleep() between frames we'd flood
         the RTP sender and Meta would drop the audio.
      2. Opus over RTP uses a 48 kHz timestamp clock regardless of internal
         codec rate. PTS must advance at 48 kHz or playout timing is wrong.
    """
    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._pts: int = 0
        self._start_ts: Optional[float] = None
        self._silence: bytes = b"\x00" * _OUT_FRAME_BYTES
        # When set, we're actively playing TTS. The receive side checks this
        # so it doesn't transcribe the bot's own voice echoing back.
        self._speaking_until: float = 0.0

    def is_speaking(self) -> bool:
        """True while bot audio is queued / recently played."""
        return asyncio.get_event_loop().time() < self._speaking_until

    async def recv(self) -> "av.AudioFrame":
        await self._pace_to_walltime()
        chunk = self._next_chunk()
        return self._build_frame(chunk)

    async def _pace_to_walltime(self) -> None:
        """Hold until this frame's scheduled emission time."""
        loop = asyncio.get_event_loop()
        if self._start_ts is None:
            self._start_ts = loop.time()
        target = self._start_ts + (self._pts + _OUT_FRAME_SAMPLES) / _OUT_RATE
        delay = target - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)

    def _next_chunk(self) -> bytes:
        """Pull one queued chunk, pad/truncate to exactly _OUT_FRAME_BYTES."""
        try:
            chunk = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return self._silence
        if len(chunk) < _OUT_FRAME_BYTES:
            return chunk + self._silence[: _OUT_FRAME_BYTES - len(chunk)]
        if len(chunk) > _OUT_FRAME_BYTES:
            return chunk[:_OUT_FRAME_BYTES]
        return chunk

    def _build_frame(self, chunk: bytes) -> "av.AudioFrame":
        """Wrap raw PCM in an av.AudioFrame and advance the PTS clock."""
        frame = av.AudioFrame(format="s16", layout="mono", samples=_OUT_FRAME_SAMPLES)
        frame.planes[0].update(chunk)
        frame.sample_rate = _OUT_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, _OUT_RATE)
        self._pts += _OUT_FRAME_SAMPLES
        return frame

    async def feed_pcm(self, pcm_24khz: bytes) -> None:
        """Queue 24 kHz int16 mono PCM (from OpenAI TTS) for playback.

        Resamples to 48 kHz and splits into 20 ms frames before queueing.
        Also marks the track as "speaking" for the duration of the audio so
        the receive side can ignore echoed-back voice.
        """
        if not pcm_24khz:
            return
        pcm_48 = _resample_pcm_24_to_48(pcm_24khz)
        for i in range(0, len(pcm_48), _OUT_FRAME_BYTES):
            self._queue.put_nowait(pcm_48[i : i + _OUT_FRAME_BYTES])

        # PCM duration plus a small tail so we don't open the mic while
        # echo is still bouncing back. When sentences are fed back-to-back
        # we extend the existing window instead of clobbering it.
        duration = len(pcm_48) / (_OUT_RATE * 2)
        now = asyncio.get_event_loop().time()
        base = max(now, self._speaking_until - 0.4)
        self._speaking_until = base + duration + 0.4


# ─────────────────────────────────────────────────────────────────────
# Tool-call filler audio
# ─────────────────────────────────────────────────────────────────────
#
# The agent uses tools that take 500 ms – 3 s to return (Mongo / Meta /
# OpenAI calls). On a live phone call that silence is jarring, so we
# hook ``RunHooks.on_tool_start`` and feed a pre-baked "one moment"
# PCM clip to the WebRTC track the instant a tool fires.
#
# Clips are TTS'd once and cached as 24 kHz int16 PCM bytes; subsequent
# tool calls reuse the cache instantly.

_FILLER_PHRASES: tuple[str, ...] = (
    "One moment, let me check that for you.",
    "Sure, give me a second.",
    "Let me pull that up for you.",
    "Hold on, checking now.",
)

_filler_cache: dict[str, bytes] = {}
_filler_cache_lock = asyncio.Lock()


async def _prewarm_fillers() -> None:
    """Synthesise every filler phrase once and stash the PCM bytes.

    Safe to call repeatedly; cached phrases are skipped. Errors are
    swallowed — a missing filler just degrades to silence (the original
    behaviour), it should never break a call.
    """
    from services.ai_services.tts_openai import synthesise_to_bytes

    async with _filler_cache_lock:
        for phrase in _FILLER_PHRASES:
            if phrase in _filler_cache:
                continue
            try:
                _filler_cache[phrase] = await synthesise_to_bytes(phrase)
            except Exception as exc:
                print(f">>> [Filler prewarm] TTS error for {phrase!r}: {exc}")


def _pick_filler_pcm() -> bytes | None:
    """Return a cached filler PCM clip, or None if the cache is cold."""
    import random
    available = [pcm for pcm in _filler_cache.values() if pcm]
    return random.choice(available) if available else None


class _ToolFillerHooks(RunHooks):
    """``RunHooks`` impl that plays a filler clip whenever a tool is invoked.

    Also flags the session when ``book_room`` completes successfully so
    the call session can arm a post-booking inactivity timer.
    """

    def __init__(self, session: "CallSession") -> None:
        self._session = session

    async def on_tool_start(self, context, agent, tool) -> None:  # type: ignore[override]
        pcm = _pick_filler_pcm()
        if not pcm:
            # Cache wasn't warm yet — fall back to a live TTS call. Slower,
            # but better than dead silence.
            try:
                from services.ai_services.tts_openai import synthesise_to_bytes
                pcm = await synthesise_to_bytes(_FILLER_PHRASES[0])
                _filler_cache[_FILLER_PHRASES[0]] = pcm
            except Exception as exc:
                print(f">>> [Filler] live TTS fallback failed: {exc}")
                return
        # Route via the session's feed queue (NOT audio_out.feed_pcm
        # directly), so this filler plays AFTER any reply sentences the
        # agent already emitted and BEFORE whatever it emits next.
        await self._session.enqueue_pcm(pcm)

    async def on_tool_end(self, context, agent, tool, result) -> None:  # type: ignore[override]
        # Only flag a SUCCESSFUL booking. book_room returns a string;
        # treat output containing "BK-" (booking id pattern) or "confirmed"
        # as success — anything else is a validation error and the caller
        # will continue the conversation.
        if getattr(tool, "name", "") != "book_room":
            return
        text = str(result) if result is not None else ""
        if "BK-" in text or "confirmed" in text.lower():
            self._session._booking_completed_this_turn = True


# ── STT singleton (shared across all concurrent calls) ───────────────

_stt_model = None


def _get_stt():
    global _stt_model
    if _stt_model is None:
        from services.ai_services.part2_stt import create_stt_model
        _stt_model = create_stt_model()
    return _stt_model


# ─────────────────────────────────────────────────────────────────────
# Call session
# ─────────────────────────────────────────────────────────────────────

class CallSession:
    """Manages one live WebRTC call end-to-end."""

    def __init__(self, call_id: str, caller: str, phone_number_id: str) -> None:
        self.call_id = call_id
        self.caller = caller
        self.phone_number_id = phone_number_id

        self.pc = RTCPeerConnection(configuration=_ICE_CONFIG)
        self.audio_out = _TtsAudioTrack()
        self.pc.addTrack(self.audio_out)

        self._audio_buf: list[np.ndarray] = []
        self._silence_count: int = 0
        self._processing = False
        # Wall-clock time when _processing was last set True. The receive
        # loop uses this to force-clear a stuck flag if a pipeline hangs
        # past _MAX_PROCESSING_SECS — without it, one wedged TTS/agent
        # call would mute the caller's mic for the rest of the call.
        self._processing_started: float = 0.0
        self._closed = False
        self._ice_connected = asyncio.Event()
        self._last_transcript: str = ""  # last caller utterance — fed into next STT prompt
        # Last-logged mute state. Used to print a one-shot "mic opened" /
        # "mic muted" transition log so we can tell from a call trace
        # whether the loop is dropping frames or no frames are arriving.
        self._was_muted: Optional[bool] = None

        # Post-booking idle hangup: when book_room completes, we arm a
        # timer. Any new speech cancels it. If it fires, the bot says
        # goodbye and the call is terminated.
        self._booking_completed_this_turn: bool = False
        self._idle_hangup_task: Optional[asyncio.Task[None]] = None
        self._idle_hangup_seconds: float = 25.0

        # Shared audio feed queue for the current agent stream. Sentences
        # from the reply AND filler clips from tool-call hooks both go
        # through here, so playback order matches submission order.
        # None when no agent stream is currently active.
        self._feed_queue: Optional[asyncio.Queue] = None

        # Warm the filler-audio cache in the background so the first
        # tool call doesn't pay TTS latency.
        asyncio.ensure_future(_prewarm_fillers())

        # Resampler — Meta sends 48 kHz Opus, our STT pipeline wants 24 kHz mono.
        # Built lazily on first frame so we adapt to whatever rate aiortc gives us.
        self._resampler: Optional["av.AudioResampler"] = None

    # ── SDP negotiation ───────────────────────────────────────────────

    async def create_answer(self, sdp_offer: str) -> str:
        """Accept the SDP offer and return an SDP answer string."""
        self._register_pc_event_handlers()

        offer = RTCSessionDescription(sdp=sdp_offer, type="offer")
        await self.pc.setRemoteDescription(offer)

        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)

        sdp = self.pc.localDescription.sdp
        self._log_local_sdp(sdp)
        return sdp

    def _register_pc_event_handlers(self) -> None:
        """Wire up aiortc events. MUST run before setRemoteDescription —
        aiortc emits the 'track' event synchronously inside that call, so
        attaching the handler afterwards loses the audio track and the
        receive loop never starts.
        """
        cid = self.call_id[:20]

        @self.pc.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            if track.kind == "audio":
                print(f">>> [Call {cid}] Audio track received")
                asyncio.ensure_future(self._receive_audio(track))

        @self.pc.on("connectionstatechange")
        async def on_state() -> None:
            state = self.pc.connectionState
            print(f">>> [Call {cid}] Connection state: {state}")
            if state in ("failed", "closed", "disconnected"):
                await self.close()

        @self.pc.on("iceconnectionstatechange")
        async def on_ice_state() -> None:
            state = self.pc.iceConnectionState
            print(f">>> [Call {cid}] ICE state: {state}")
            if state in ("connected", "completed"):
                self._ice_connected.set()

        @self.pc.on("icegatheringstatechange")
        async def on_ice_gather() -> None:
            print(f">>> [Call {cid}] ICE gathering: {self.pc.iceGatheringState}")

    def _log_local_sdp(self, sdp: str) -> None:
        """Print ICE candidates and DTLS fingerprints from the local answer."""
        cid = self.call_id[:20]
        cand_lines = [l for l in sdp.split("\r\n") if l.startswith("a=candidate")]
        fp_lines = [l for l in sdp.split("\r\n") if l.startswith("a=fingerprint:")]
        print(f">>> [Call {cid}] Local SDP has {len(cand_lines)} ICE candidate(s):")
        for c in cand_lines:
            print(f">>>   {c}")
        if not cand_lines:
            print(f">>> [Call {cid}] ⚠️  NO ICE candidates — server has no usable network interface!")
        fp_algos = [l.split()[0].split(":", 1)[1] for l in fp_lines]
        print(f">>> [Call {cid}] Fingerprints ({len(fp_lines)}): {fp_algos} "
              f"(non-sha256 will be filtered before send)")

    async def wait_for_ice_connected(self, timeout: float = 15.0) -> bool:
        """Block until ICE reports a working media path, or timeout elapses."""
        try:
            await asyncio.wait_for(self._ice_connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            print(f">>> [Call {self.call_id[:20]}] ICE did not connect within {timeout}s")
            return False

    # ── Audio receive loop ─────────────────────────────────────────────

    async def _receive_audio(self, track: MediaStreamTrack) -> None:
        cid = self.call_id[:20]
        print(f">>> [Call {cid}] Audio receive loop started")

        self._frame_count = 0
        self._rms_window: list[float] = []
        self._speech_frames = 0

        while not self._closed:
            try:
                frame = await track.recv()
            except Exception as exc:
                # MediaStreamError on track end, or anything else — stop
                # the loop. Without this, the outer try used to swallow
                # the end-of-track error and exit silently; we surface it
                # so the call's lifecycle is observable in logs.
                if not self._closed:
                    print(f">>> [Call {cid}] track.recv() ended: {exc}")
                return

            try:
                self._ensure_resampler(frame)
                resampled = self._resampler.resample(frame)
                if not resampled:
                    continue

                muted = self._should_skip_inbound()
                self._log_mute_transition(muted)
                if muted:
                    self._reset_utterance_state()
                    continue

                for rf in resampled:
                    self._classify_resampled_frame(rf)

                self._maybe_flush_utterance()
            except Exception as exc:
                # Don't let one bad frame kill the receive loop — that
                # silently strands the caller with no way to be heard.
                if not self._closed:
                    print(f">>> [Call {cid}] Frame processing error: {exc}")
                    traceback.print_exc()
                continue

    def _log_mute_transition(self, muted: bool) -> None:
        """Print a one-shot log line when the mic flips muted↔open.

        Without this, you can't tell from a stuck-call trace whether
        frames are arriving and being dropped (mic muted) or not arriving
        at all (track silent). Logged once per transition, not per frame.
        """
        if self._was_muted is muted:
            return
        self._was_muted = muted
        cid = self.call_id[:20]
        if muted:
            reason = "speaking" if self.audio_out.is_speaking() else "processing"
            print(f">>> [Call {cid}] mic MUTED ({reason})")
        else:
            print(f">>> [Call {cid}] mic OPEN — listening for caller")

    def _ensure_resampler(self, frame: "av.AudioFrame") -> None:
        """Lazily build the 48k→24k resampler from the first inbound frame."""
        if self._resampler is not None:
            return
        cid = self.call_id[:20]
        src_rate = frame.sample_rate
        src_layout = frame.layout.name if hasattr(frame.layout, "name") else "mono"
        print(f">>> [Call {cid}] Incoming audio: {src_rate} Hz {src_layout} → resampling to {_SAMPLE_RATE} Hz mono")
        self._resampler = av.AudioResampler(
            format="s16", layout="mono", rate=_SAMPLE_RATE
        )

    def _should_skip_inbound(self) -> bool:
        """True when bot is talking or busy — drop mic input to avoid self-echo.

        Also force-clears a stuck _processing flag: if a pipeline hung
        past _MAX_PROCESSING_SECS (e.g. a wedged TTS or agent call that
        slipped past its own timeout), without this the mic would stay
        muted for the rest of the call.
        """
        if self._processing and self._processing_started:
            elapsed = asyncio.get_event_loop().time() - self._processing_started
            if elapsed > _MAX_PROCESSING_SECS:
                print(
                    f">>> [Call {self.call_id[:20]}] _processing stuck "
                    f"{elapsed:.1f}s — force-clearing flag"
                )
                self._processing = False
                self._processing_started = 0.0
        return self.audio_out.is_speaking() or self._processing

    def _reset_utterance_state(self) -> None:
        """Clear in-flight buffer + counters (used after speak / on flush)."""
        self._audio_buf.clear()
        self._silence_count = 0
        self._speech_frames = 0

    def _classify_resampled_frame(self, rf: "av.AudioFrame") -> None:
        """Append one resampled frame to the buffer as speech or trailing silence."""
        arr = np.frombuffer(bytes(rf.planes[0]), dtype=np.int16)
        rms = _rms(arr)
        self._rms_window.append(rms)
        self._frame_count += 1

        if rms > _SILENCE_RMS:
            # Caller spoke — cancel a pending post-booking hangup, if any.
            if self._idle_hangup_task is not None:
                self._cancel_idle_hangup_timer()
            self._audio_buf.append(arr)
            self._silence_count = 0
            self._speech_frames += 1
        else:
            self._silence_count += 1
            if self._audio_buf:
                self._audio_buf.append(arr)  # include trailing silence

        if self._frame_count % _RMS_LOG_INTERVAL == 0 and self._rms_window:
            self._log_mic_stats()

    def _log_mic_stats(self) -> None:
        cid = self.call_id[:20]
        avg = sum(self._rms_window) / len(self._rms_window)
        peak = max(self._rms_window)
        self._rms_window.clear()
        print(
            f">>> [Call {cid}] mic avg={avg:.0f} peak={peak:.0f} "
            f"thr={_SILENCE_RMS:.0f} buf_frames={len(self._audio_buf)} "
            f"silence={self._silence_count} speech={self._speech_frames}"
        )

    def _maybe_flush_utterance(self) -> None:
        """If end-of-speech detected, ship the buffer to STT (or discard noise)."""
        if not self._audio_buf:
            return
        buf_secs = sum(len(a) for a in self._audio_buf) / _SAMPLE_RATE
        end_of_speech = self._silence_count >= _SILENCE_FRAMES
        too_long = buf_secs >= _MAX_BUFFER_SECS

        if not (end_of_speech or too_long):
            return

        if self._speech_frames < _MIN_SPEECH_FRAMES:
            # Mostly background noise — don't burn an STT call on it.
            self._reset_utterance_state()
            return

        cid = self.call_id[:20]
        trigger = "silence" if end_of_speech else "max-buffer"
        print(
            f">>> [Call {cid}] End of utterance ({trigger}): "
            f"{buf_secs:.1f}s captured, {self._speech_frames} speech frames → STT"
        )
        audio = np.concatenate(self._audio_buf)
        self._reset_utterance_state()
        # Flip the flag synchronously, BEFORE scheduling the task. If we
        # only set it inside _process_utterance, the receive loop can
        # detect a second utterance in the gap before the task runs and
        # spawn a parallel pipeline that races on _processing.
        self._processing = True
        self._processing_started = asyncio.get_event_loop().time()
        asyncio.ensure_future(self._process_utterance(audio))

    # ── STT → Agent → TTS pipeline ────────────────────────────────────

    async def _process_utterance(self, audio: np.ndarray) -> None:
        # _maybe_flush_utterance already set _processing synchronously,
        # but set it again here for any direct caller and to refresh the
        # watchdog timer so the elapsed clock starts when work begins.
        self._processing = True
        self._processing_started = asyncio.get_event_loop().time()
        cid = self.call_id[:20]
        duration = len(audio) / _SAMPLE_RATE
        print(f">>> [Call {cid}] Utterance captured: {duration:.1f}s, {len(audio)} samples")
        try:
            transcript = await self._transcribe(audio)
            if not transcript or not transcript.strip():
                print(f">>> [Call {cid}] Empty transcript — ignoring utterance")
                return

            await self._stream_agent_and_speak(transcript)

        except Exception as exc:
            print(f">>> [Call {cid}] Pipeline error: {exc}")
            traceback.print_exc()
            await self._speak_error_apology()
        finally:
            self._processing = False
            self._processing_started = 0.0

    def _build_stt_prompt(self) -> str:
        """Build a dynamic Groq Whisper prompt for the current turn.

        Groq Whisper treats the prompt as a preceding-transcript context hint.
        Each call we inject:
          1. Today's date + current time (PKT) — grounds temporal references
             like "tomorrow" or "next Friday" in the right calendar context.
          2. Last caller utterance — gives the decoder conversation continuity
             so it picks the right vocabulary for the next turn.
        The result reads like natural speech, which is how Whisper expects it.
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo
        PKT = ZoneInfo("Asia/Karachi")
        now = datetime.now(PKT)
        date_str = now.strftime("%-d %B %Y")   # e.g. "13 May 2026"
        time_str = now.strftime("%-I:%M %p")    # e.g. "3:30 PM"

        parts = [f"Today is {date_str} and the time is {time_str}."]
        if self._last_transcript:
            parts.append(self._last_transcript)
        return " ".join(parts)

    async def _transcribe(self, audio: np.ndarray) -> str:
        """Preprocess and transcribe a 24 kHz mono buffer via Groq Whisper.

        Preprocessing chain applied before STT:
        - Downsample 24 kHz → 16 kHz (Whisper native; phone audio has no content above 8 kHz anyway)
        - Normalise volume (quiet callers, AGC variations on WebRTC path)
        - High-pass at 80 Hz (removes low-frequency WebRTC/Opus codec rumble)
        - Strip leading/trailing silence (≥300 ms dead air the VAD left on the edges)
        """
        from pydub import AudioSegment
        from pydub.effects import normalize, high_pass_filter, strip_silence
        from agents.voice import AudioInput, STTModelSettings

        # numpy (24 kHz) → pydub
        segment = AudioSegment(
            audio.tobytes(),
            frame_rate=_SAMPLE_RATE,
            sample_width=2,
            channels=1,
        )
        # Downsample, denoise, normalise
        segment = segment.set_frame_rate(16_000)
        segment = normalize(segment, headroom=0.1)
        segment = high_pass_filter(segment, cutoff=80)
        segment = strip_silence(segment, silence_len=300, silence_thresh=-40, padding=50)

        audio_16k = np.frombuffer(segment.raw_data, dtype=np.int16)

        raw = await _get_stt().transcribe(
            input=AudioInput(buffer=audio_16k, frame_rate=16_000),
            settings=STTModelSettings(prompt=self._build_stt_prompt()),
            trace_include_sensitive_data=False,
            trace_include_sensitive_audio_data=False,
        )
        transcript = _normalise_transcript(raw)
        if transcript != raw:
            print(f">>> [Call {self.call_id[:20]}] STT (raw): {raw!r}")
        print(f">>> [Call {self.call_id[:20]}] STT: {transcript!r}")

        # Update rolling context for the next turn's prompt
        if transcript.strip():
            self._last_transcript = transcript
        return transcript

    async def _stream_agent_and_speak(self, transcript: str) -> None:
        """Stream the agent reply and feed each sentence's TTS to the call.

        Pipeline (all interleaved):
          agent token stream → sentence buffer → per-sentence TTS task
          → background feeder awaits tasks in order → audio_out.feed_pcm

        A separate ``_ToolFillerHooks`` instance feeds a pre-baked
        "one moment" clip onto the same track whenever the agent invokes
        a tool, so the caller hears something during the tool's
        network round-trip too.
        """
        from services.ai_services.agent import get_agent_response_stream
        from services.ai_services.tts_openai import synthesise_to_bytes

        cid = self.call_id[:20]
        buffer = ""
        full_parts: list[str] = []
        stripped_voice_tag = False

        async def _tts_one(sentence: str) -> bytes:
            try:
                return await asyncio.wait_for(
                    synthesise_to_bytes(sentence),
                    timeout=_TTS_TIMEOUT_SECS,
                )
            except asyncio.TimeoutError:
                print(
                    f">>> [Call {cid}] TTS timed out after "
                    f"{_TTS_TIMEOUT_SECS:.0f}s: {sentence[:40]!r}"
                )
                return b""
            except Exception as exc:
                print(f">>> [Call {cid}] TTS error on sentence: {exc}")
                return b""

        # Background feeder: pulls awaitables off the queue, awaits each
        # in submission order, and feeds the PCM to the WebRTC track.
        # Stored on self so the tool-filler hook can enqueue filler PCM
        # into the same queue (avoiding the ordering bug where filler
        # would skip ahead of earlier reply sentences).
        self._feed_queue = asyncio.Queue()
        feed_queue = self._feed_queue  # local alias for readability

        async def _feeder() -> None:
            while True:
                item = await feed_queue.get()
                if item is None:
                    return
                pcm = await item
                if pcm:
                    await self.audio_out.feed_pcm(pcm)

        feeder_task = asyncio.create_task(_feeder())
        hooks = _ToolFillerHooks(self)
        sentence_count = 0
        # Reset per-turn flag; the hook will set it if book_room succeeds.
        self._booking_completed_this_turn = False

        # Wall-clock deadline for the agent stream. If the LLM stalls
        # mid-stream we abandon what we have rather than hang the call.
        stream_deadline = (
            asyncio.get_event_loop().time() + _AGENT_STREAM_TIMEOUT_SECS
        )

        async def _drain_stream() -> None:
            nonlocal buffer, sentence_count, stripped_voice_tag
            async for delta in get_agent_response_stream(
                user_message=transcript,
                whatsapp_number=self.caller,
                channel="whatsapp_call",
                hooks=hooks,
                # Per-call session id — each call starts with empty history
                # and never inherits chat or prior-call context.
                session_id=f"call:{self.call_id}",
            ):
                if not delta:
                    continue

                # Strip a possible leading [SEND_VOICE] tag.
                if not stripped_voice_tag:
                    buffer += delta
                    full_parts.append(delta)
                    if buffer.lstrip().startswith("[SEND_VOICE]"):
                        if "]" in buffer:
                            buffer = buffer.split("]", 1)[1].lstrip()
                            stripped_voice_tag = True
                    elif len(buffer) > len("[SEND_VOICE]"):
                        stripped_voice_tag = True
                else:
                    buffer += delta
                    full_parts.append(delta)

                sentences, buffer = _split_off_sentences(buffer)
                for sentence in sentences:
                    sentence_count += 1
                    await feed_queue.put(asyncio.create_task(_tts_one(sentence)))

        try:
            try:
                # wait_for cancels _drain_stream on timeout; the surrounding
                # finally still runs, so the feeder is drained cleanly.
                await asyncio.wait_for(
                    _drain_stream(),
                    timeout=max(1.0, stream_deadline - asyncio.get_event_loop().time()),
                )
            except asyncio.TimeoutError:
                print(
                    f">>> [Call {cid}] Agent stream exceeded "
                    f"{_AGENT_STREAM_TIMEOUT_SECS:.0f}s — flushing partial reply"
                )

            # Flush trailing fragment (no terminal punctuation).
            tail = buffer.strip()
            if tail:
                sentence_count += 1
                await feed_queue.put(asyncio.create_task(_tts_one(tail)))

            full_reply = "".join(full_parts).strip()
            if full_reply.startswith("[SEND_VOICE]"):
                full_reply = full_reply[len("[SEND_VOICE]"):].lstrip()
            print(f">>> [Call {cid}] Agent: {full_reply!r}  ({sentence_count} sentences)")

            if sentence_count == 0:
                await self._speak("Sorry, I didn't catch that. Could you say it again?")
                return
        finally:
            # Sentinel — tells the feeder to drain remaining queued items
            # and exit. We await it so playback finishes before we return.
            await feed_queue.put(None)
            await feeder_task
            # Detach the shared queue so any stray late-firing hook is
            # safely a no-op rather than enqueueing into a dead feeder.
            self._feed_queue = None

            # If book_room succeeded this turn, start the inactivity timer
            # now that the bot has finished speaking the confirmation.
            if self._booking_completed_this_turn:
                self._arm_idle_hangup_timer()

    # ── ordered audio enqueue (used by tool-filler hook) ──────────────

    async def enqueue_pcm(self, pcm: bytes) -> None:
        """Queue raw PCM into the active agent-stream's feeder.

        Fillers (and any other async audio that originates outside the
        sentence pipeline) MUST go through here instead of calling
        ``audio_out.feed_pcm`` directly, otherwise they can land in the
        WebRTC queue ahead of earlier reply sentences whose TTS hasn't
        finished yet — causing the "plays out of order / overlaps"
        symptom on the call.

        No-op if no stream is currently active.
        """
        if not pcm or self._feed_queue is None:
            return
        fut: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()
        fut.set_result(pcm)
        await self._feed_queue.put(fut)

    # ── post-booking inactivity hangup ────────────────────────────────

    def _arm_idle_hangup_timer(self) -> None:
        """Start (or restart) the post-booking inactivity timer.

        Cancels any existing timer first, then spawns a task that will
        wait ``_idle_hangup_seconds``. If no speech cancels it in that
        window, the task speaks a goodbye and hangs up the call.
        """
        self._cancel_idle_hangup_timer()
        if self._closed:
            return
        cid = self.call_id[:20]
        print(
            f">>> [Call {cid}] Booking complete — arming "
            f"{self._idle_hangup_seconds:.0f}s idle hangup timer"
        )
        self._idle_hangup_task = asyncio.create_task(self._idle_hangup_runner())

    def _cancel_idle_hangup_timer(self) -> None:
        """Cancel a pending idle-hangup task (caller spoke or call closed)."""
        task = self._idle_hangup_task
        if task is not None and not task.done():
            task.cancel()
        self._idle_hangup_task = None

    async def _idle_hangup_runner(self) -> None:
        """Sleep, then say goodbye and terminate the call."""
        cid = self.call_id[:20]
        try:
            await asyncio.sleep(self._idle_hangup_seconds)
        except asyncio.CancelledError:
            print(f">>> [Call {cid}] Idle hangup cancelled (caller spoke)")
            return

        if self._closed:
            return

        print(f">>> [Call {cid}] Idle hangup firing — no speech after booking")
        try:
            await self._speak("Thanks for booking with The Grand Dine. Goodbye!")
            # Give the audio a moment to actually play out before we tear
            # down the WebRTC track.
            while self.audio_out.is_speaking():
                await asyncio.sleep(0.2)
        except Exception as exc:
            print(f">>> [Call {cid}] Goodbye speech failed: {exc}")

        try:
            await terminate_call(self.call_id, self.caller)
        except Exception as exc:
            print(f">>> [Call {cid}] terminate_call failed: {exc}")

    async def _speak(self, text: str) -> None:
        """Synthesise text and feed it to the outbound WebRTC track."""
        from services.ai_services.tts_openai import synthesise_to_bytes

        pcm = await synthesise_to_bytes(text)
        await self.audio_out.feed_pcm(pcm)
        print(f">>> [Call {self.call_id[:20]}] TTS sent {len(pcm)} bytes")

    async def _speak_error_apology(self) -> None:
        """Best-effort spoken apology so the caller never hears dead silence."""
        try:
            await self._speak("Sorry, I'm having trouble right now. Could you try again?")
        except Exception:
            pass

    # ── Cleanup ────────────────────────────────────────────────────────

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cancel_idle_hangup_timer()
            await self.pc.close()
            _ACTIVE_CALLS.pop(self.call_id, None)
            # Drop the per-call Upstash session so it doesn't linger for
            # the full 1-hour TTL after hangup.
            try:
                from services.ai_services.upstash_memory import get_session
                await get_session(f"call:{self.call_id}").clear_session()
            except Exception as exc:
                print(f">>> [Call {self.call_id[:20]}] Session clear failed: {exc}")
            print(f">>> [Call {self.call_id[:20]}] Session closed")


# ─────────────────────────────────────────────────────────────────────
# Meta Calling API helpers
# ─────────────────────────────────────────────────────────────────────

def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


# WhatsApp Calling currently requires Graph v23.0 — older versions return
# obscure SDP validation errors. Calling-only override; other endpoints keep
# whatever WHATSAPP_API_BASE points at.
_CALLING_API_BASE = "https://graph.facebook.com/v23.0"
_CALLS_URL = f"{_CALLING_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/calls"


def _filter_sdp_for_whatsapp(sdp: str) -> str:
    """Strip SDP lines Meta's calling validator rejects.

    Meta only accepts SHA-256 DTLS fingerprints. aiortc emits SHA-256 AND
    SHA-384 AND SHA-512 by default — the presence of the longer hashes
    causes a 400 "SDP Validation error" (code 138008 / subcode 2593093).
    """
    out: list[str] = []
    for line in sdp.splitlines():
        if line.startswith("a=fingerprint:") and not line.startswith("a=fingerprint:sha-256"):
            continue
        out.append(line)
    return "\r\n".join(out) + "\r\n"


async def _post_call_action(payload: dict, label: str) -> bool:
    """POST a single action to /{phone_number_id}/calls. Returns True on 200/success."""
    async with httpx.AsyncClient(timeout=15) as client:
        print(f">>> [Calling API] POST {label} → {_CALLS_URL}")
        resp = await client.post(_CALLS_URL, json=payload, headers=_auth_headers())
        snippet = resp.text[:400]
        print(f">>> [Calling API] {label} {resp.status_code}: {snippet}")

        if resp.status_code != 200:
            print(f">>> [Calling API] {label} FAILED — full body: {resp.text}")
            return False

        try:
            body = resp.json()
        except Exception:
            return True
        if body.get("success") is False:
            print(f">>> [Calling API] {label} returned success=false: {body}")
            return False
        return True


async def send_pre_accept(call_id: str, sdp_answer: str, caller: str) -> bool:
    """Pre-accept — opens the media path before signaling 'answered'."""
    sdp_answer = _filter_sdp_for_whatsapp(sdp_answer)
    return await _post_call_action(
        {
            "messaging_product": "whatsapp",
            "to": caller,
            "action": "pre_accept",
            "call_id": call_id,
            "session": {"sdp": sdp_answer, "sdp_type": "answer"},
        },
        "pre_accept",
    )


async def send_accept(call_id: str, sdp_answer: str, caller: str) -> bool:
    """Accept — caller now hears connection. Must follow pre_accept."""
    sdp_answer = _filter_sdp_for_whatsapp(sdp_answer)
    return await _post_call_action(
        {
            "messaging_product": "whatsapp",
            "to": caller,
            "action": "accept",
            "call_id": call_id,
            "session": {"sdp": sdp_answer, "sdp_type": "answer"},
        },
        "accept",
    )


async def reject_call(call_id: str, caller: str = "") -> None:
    """Reject an incoming call before answering it."""
    await _post_call_action(
        {
            "messaging_product": "whatsapp",
            "to": caller,
            "action": "reject",
            "call_id": call_id,
        },
        "reject",
    )


async def terminate_call(call_id: str, caller: str = "") -> None:
    """Hang up an active call."""
    session = _ACTIVE_CALLS.get(call_id)
    if session:
        await session.close()
    await _post_call_action(
        {
            "messaging_product": "whatsapp",
            "to": caller,
            "action": "terminate",
            "call_id": call_id,
        },
        "terminate",
    )


# ─────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────

async def handle_call_event(caller: str, call_data: dict) -> None:
    """Dispatch a call webhook event.

    Parameters
    ----------
    caller : str
        The caller's WhatsApp phone number.
    call_data : dict
        The call object from the webhook payload.
    """
    if not _AIORTC_AVAILABLE:
        print(">>> [Calling] aiortc unavailable — cannot answer call")
        return

    call_id = call_data.get("id", "")
    event = call_data.get("event", "")
    sdp_offer = call_data.get("session", {}).get("sdp", "")

    print(f">>> [Calling] event={event!r} call_id={call_id[:30]} caller={caller}")

    if event == "connect" and sdp_offer:
        await _handle_connect(call_id, caller, sdp_offer)
    elif event in ("terminate", "disconnect", "hangup"):
        await _handle_terminate(call_id)
    else:
        print(f">>> [Calling] Unhandled call event={event!r}")


async def _handle_connect(call_id: str, caller: str, sdp_offer: str) -> None:
    """Answer an incoming call: SDP exchange, pre_accept, ICE wait, accept, greeting."""
    if call_id in _ACTIVE_CALLS:
        print(f">>> [Calling] Duplicate connect for {call_id[:20]} — ignoring")
        return

    session = CallSession(
        call_id=call_id,
        caller=caller,
        phone_number_id=WHATSAPP_PHONE_NUMBER_ID,
    )
    _ACTIVE_CALLS[call_id] = session

    # Kick off greeting TTS the moment the call arrives — it runs in parallel
    # with SDP exchange + pre_accept + ICE + accept, so the bytes are ready
    # the instant Meta returns 200.
    greeting_task = asyncio.create_task(_get_greeting_pcm())

    try:
        sdp_answer = await session.create_answer(sdp_offer)
        print(f">>> [Calling] SDP answer ready, sending pre_accept …")

        if not await _do_pre_accept(call_id, sdp_answer, caller, session):
            return

        await _wait_for_media_path(session)

        if not await _do_accept(call_id, sdp_answer, caller, session):
            return

        print(f">>> [Calling] Call answered: {call_id[:30]}")
        await _play_cached_greeting(session, greeting_task)
    except Exception as exc:
        print(f">>> [Calling] Failed to answer call: {exc}")
        traceback.print_exc()
        await session.close()


async def _handle_terminate(call_id: str) -> None:
    """Close the local session on hangup. (Meta has already torn down its side.)"""
    session = _ACTIVE_CALLS.get(call_id)
    if session:
        await session.close()
    print(f">>> [Calling] Call ended: {call_id[:30]}")


async def _do_pre_accept(
    call_id: str, sdp_answer: str, caller: str, session: "CallSession"
) -> bool:
    """Send pre_accept; close session and return False on failure."""
    if await send_pre_accept(call_id, sdp_answer, caller):
        return True
    print(f">>> [Calling] pre_accept rejected — aborting call {call_id[:20]}")
    await session.close()
    return False


async def _do_accept(
    call_id: str, sdp_answer: str, caller: str, session: "CallSession"
) -> bool:
    """Send accept; close session and return False on failure."""
    if await send_accept(call_id, sdp_answer, caller):
        return True
    print(f">>> [Calling] accept rejected — closing call {call_id[:20]}")
    await session.close()
    return False


async def _wait_for_media_path(session: "CallSession") -> None:
    """Wait for ICE before sending accept (avoids clipping the first word)."""
    if not await session.wait_for_ice_connected(timeout=15.0):
        print(">>> [Calling] No media path — sending accept anyway")


async def _play_cached_greeting(
    session: "CallSession", greeting_task: "asyncio.Task[bytes]"
) -> None:
    """Feed the pre-synthesised greeting bytes to the WebRTC track."""
    try:
        pcm = await greeting_task
        await session.audio_out.feed_pcm(pcm)
        print(f">>> [Call {session.call_id[:20]}] Greeting played ({len(pcm)} bytes)")
    except Exception as gexc:
        print(f">>> [Call {session.call_id[:20]}] Greeting failed: {gexc}")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _rms(arr: np.ndarray) -> float:
    return float(math.sqrt(np.mean(arr.astype(np.float64) ** 2)))
