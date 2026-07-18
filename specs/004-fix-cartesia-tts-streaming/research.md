# Phase 0 Research: Clear Cartesia Voice Playback & Real-Time Streaming Loop

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Feature**: 004-fix-cartesia-tts-streaming | **Date**: 2026-07-07

This document resolves the technical unknowns behind the spec's requirements, grounded in
the actual 003 code and in current SDK docs fetched via Context7 (constitution VI).

---

## `1. Root cause of the "old TV / toun toush" distortion (US1 / FR-001, FR-002)

**Decision**: Reframe outbound audio into fixed **20 ms mono frames at 48 kHz** before it
reaches aiortc's Opus encoder, using a persistent `av.AudioResampler(rate=48000)` plus a
byte buffer that emits exactly `48000 * 0.02 = 960` samples (1920 bytes s16) per frame, with
monotonic `pts`. Cartesia continues to synthesize `pcm_s16le`; the bridge owns the
resample-and-repacketize step.

**Rationale**: Today `_OutboundTrack.recv()` (`media/webrtc.py`) does:
`av.AudioFrame(format="s16", layout="mono", samples=len(pcm)//2)` with
`frame.sample_rate = 16000` — **one frame per Cartesia chunk of arbitrary length**. aiortc's
Opus encoder (`aiortc.codecs.opus`) requires fixed 20 ms frames at 48 kHz; when it is fed
irregular, oddly-sized 16 kHz frames it must both resample and re-slice on the fly, and the
seams between mismatched frames produce exactly the periodic clicking/buzzing ("toun toush")
and stutter the caller reports. The frame `pts`/`time_base` are also expressed in 16 kHz
units while the encoder works at 48 kHz, compounding timing drift. Emitting clean, constant
20 ms/48 kHz frames removes the resample-at-encode seams and the timing mismatch — the single
highest-impact fix.

**Alternatives considered**:
- *Set Cartesia `output_format.sample_rate = 48000` and keep one-frame-per-chunk*: removes
  the rate mismatch but NOT the irregular-frame-size problem; Opus still receives non-20 ms
  frames → still glitchy. Rejected.
- *Let aiortc resample and hope*: current behavior; it is the bug. Rejected.
- *Switch outbound to a `MediaPlayer`/file*: breaks real-time streaming and barge-in. Rejected.

**Note on Cartesia rate**: We raise `tts_output_sample_rate` to 48000 so the resampler does a
no-op rate change (still buffered into 20 ms frames), avoiding an extra 16 k→48 k upsample and
keeping the highest fidelity. The buffering/repacketization is what actually fixes the glitch.

---

## `2. Real-time outbound pacing & barge-in flush (FR-009, FR-013)

**Decision**: Keep the track responsible for real-time pacing (sleep until wall-clock catches
the cumulative sample timestamp — the existing pattern is correct once frames are 20 ms). Add
`_OutboundTrack.flush()` that drains the pending queue and resets the buffer, and expose
`MediaBridge.stop_playback()` so the session can interrupt an in-progress reply the moment
caller speech is detected.

**Rationale**: Barge-in (clarified = required this release) needs playback to stop within
500 ms (SC-008). Because playback is a queue of paced 20 ms frames, "stop" = clear the queue
and let `recv()` block on the next (now empty) queue; no half-frame is emitted, so no new
artifact is introduced. Caller-speech detection reuses the STT interim signal (see `3) — the
first non-empty interim transcript (or VAD event) during playback triggers `stop_playback()`.

**Alternatives considered**:
- *Cancel the whole synthesize task only*: leaves already-queued frames playing → caller
  still hears the tail over their own speech. Rejected; must also flush the queue.

---

## `3. Silence-based endpointing to finalize a caller turn (US2 / FR-005, FR-012)

**Decision**: Configure Deepgram `listen.v1.connect(...)` with `endpointing=<stt_endpointing_ms>`
(default **800 ms**), `interim_results=True`, `utterance_end_ms="1000"`, `vad_events=True`.
A turn finalizes on `speech_final: true`; `UtteranceEnd` is a backstop. Interim
(`is_final=False`, non-empty) segments are surfaced so the session can trigger barge-in.

**Rationale**: The clarification set turn finalization to a ~0.7–1 s pause. Context7
(developers.deepgram.com/docs/endpointing, /docs/utterance-end) confirms `endpointing` is the
"milliseconds of silence before finalizing" knob that flips `speech_final: true`, and that
`utterance_end_ms` + `vad_events` + `interim_results=True` provide the `UtteranceEnd` backstop
for noisy lines. Current code sets neither `endpointing` nor `utterance_end_ms` (so it relies
on the ~10 ms default, finalizing too eagerly and cutting callers off). Setting 800 ms matches
the clarified behavior and keeps `stt.py` swappable (only the `connect(...)` kwargs change).

**Alternatives considered**:
- *Client-side VAD timer in the session*: duplicates what Deepgram already does server-side
  and adds a second source of truth. Rejected.
- *Deepgram Flux `listen.v2` end-of-turn*: the installed 7.4.0 Flux client only accepts
  `flux-general-*` models and lacks `send_finalize`; staying on v1 preserves `nova-3`. Deferred.

---

## `4. Streaming agent reply → early TTS (US3 / FR-007, FR-008)

**Decision**: Add `run_turn_stream(...)` in `agent/service.py` using `Runner.run_streamed(...)`
and iterating `result.stream_events()`, yielding `event.data.delta` for events where
`event.type == "raw_response_event"` and `isinstance(event.data, ResponseTextDeltaEvent)`.
`session._handle_turn` pipes that async text generator directly into
`synthesize_stream(text_chunks=..., call_id=...)` (which already accepts an
`AsyncIterator[str]` and `ctx.push`-es each piece), so Cartesia begins synthesizing — and the
bridge begins playing — on the first token portion. The full reply text is still assembled in
parallel for `log_turn`.

**Rationale**: Context7 (openai-agents-python/docs/streaming.md) confirms `Runner.run_streamed`
+ `stream_events()` + filtering `ResponseTextDeltaEvent.delta` is the supported token-delta
path, and that the stream must be fully consumed so session persistence/history completes.
`tts.synthesize_stream` was already built to accept streamed text — this closes the last gap
so replies start playing < 1.5 s after the caller stops (SC-005) instead of after the whole
answer is generated. Keeps the agent stage behind its interface (Principle IV).

**Alternatives considered**:
- *Sentence-buffer the reply, synthesize per sentence*: adds latency to the first word and a
  buffering layer; token-delta streaming into Cartesia's incremental context is simpler and
  faster. Rejected.
- *Keep non-streaming `Runner.run`*: violates FR-007/FR-008. Rejected.

---

## `5. Provider-failure behavior (FR-011)

**Decision**: Wrap each STT/TTS provider operation with **retry-once** (`provider_retry_attempts`,
default 1 extra attempt). If the retry also fails, the session speaks a short apology
("Sorry, I didn't catch that — could you say it again?") via a guaranteed-simple TTS call and
**continues the loop** rather than tearing the call down. STT stream failure recovers by
re-establishing the transcribe stream for the next turn instead of ending the call.

**Rationale**: Matches the clarification (retry once silently → spoken apology → continue) and
constitution IV (failures handled explicitly, never silently dropped). Today `TtsError` is
logged and the turn goes silent, and `SttError` ends the whole call — neither matches the
clarified contract.

**Alternatives considered**:
- *Unbounded retries*: risks long dead air on a real outage. Rejected in favor of one retry
  then graceful apology.

---

## `6. No-input handling (FR-014)

**Decision**: Keep the existing re-prompt-once-then-terminate loop in `session.py`; expose the
timing via `caller_silence_timeout_s` (default lowered to ~7 s to sit in the clarified 5–8 s
window). No structural change.

**Rationale**: The 003 loop already implements "re-prompt once, then hang up gracefully,"
which matches the clarification; only the timeout default needs alignment.

---

## Resolved unknowns summary

| Unknown | Resolution |
|---------|-----------|
| Why does playback sound like an "old TV"? | Irregular, non-20 ms / wrong-rate frames handed to Opus encoder (§1). |
| How to fix it? | Buffer + resample to fixed 20 ms @ 48 kHz frames with monotonic pts (§1). |
| How to finalize a turn on a pause? | Deepgram `endpointing=800`, `utterance_end_ms`, `vad_events` (§3). |
| How to start TTS before the reply is done? | `Runner.run_streamed` token deltas → `synthesize_stream` (§4). |
| How to stop playback on barge-in < 500 ms? | Interim STT signal → `MediaBridge.stop_playback()` flush (§2). |
| How to handle a provider blip? | Retry once, then spoken apology, then continue (§5). |

No NEEDS CLARIFICATION markers remain.
