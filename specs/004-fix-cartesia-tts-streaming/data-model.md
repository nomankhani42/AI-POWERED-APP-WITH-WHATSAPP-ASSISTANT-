# Phase 1 Data Model: Clear Cartesia Voice Playback & Real-Time Streaming Loop

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Feature**: 004-fix-cartesia-tts-streaming | **Date**: 2026-07-07

This feature adds **no new durable entities**. Persistent records (`Call`, `CallEvent` in
MongoDB) are unchanged from feature 003. What changes is the *behavior and framing* of the
transient in-memory values that flow through the live pipeline, plus new configuration and a
per-call playback state. Entities below are transient unless marked persistent.

---

## Transient pipeline values (`src/app/services/media/types.py`)

### TranscriptSegment (existing — semantics clarified)
| Field | Type | Notes |
|-------|------|-------|
| `call_id` | str | Owning call. |
| `text` | str | Recognized text for this segment. |
| `is_final` | bool | `True` only when Deepgram endpointing flips `speech_final` after ~800 ms silence (§3). **Interim (`False`, non-empty) segments now additionally signal caller speech for barge-in.** |
| `ts` | float | Emission time (epoch seconds). |

**State transitions**: `interim (is_final=False)* → final (is_final=True)` per utterance. A
final segment with non-empty `text` triggers exactly one agent turn (FR-005/FR-012).

### SpeechChunk (existing — framing rules added)
| Field | Type | Notes |
|-------|------|-------|
| `call_id` | str | Owning call. |
| `audio` | bytes | `pcm_s16le` mono from Cartesia. Arbitrary length as received. |
| `sample_rate` | int | Now **48000** (`tts_output_sample_rate`), matching the Opus wire rate (`1/`4). |

**Invariant (new)**: `SpeechChunk.audio` is *not* played directly. The media bridge buffers
it and re-emits fixed **20 ms (960-sample / 1920-byte) mono frames at 48 kHz** to the encoder
(FR-002). Chunk boundaries carry no timing meaning after buffering.

### ConversationTurn (existing — unchanged)
Turn `0` = welcome; each later turn pairs a caller transcript with the agent reply. Logged,
never persisted. The `reply` field is now the *fully assembled* streamed text (deltas joined).

### AgentTextDelta (new, transient — not a stored type)
Represents one streamed token piece from the agent. Modeled as a plain `str` yielded by
`run_turn_stream(...)` and consumed by `synthesize_stream(...)`; no dataclass required.
Ordering MUST be preserved end to end (FR-009).

---

## Per-call playback state (`_OutboundTrack` / `MediaBridge`, `media/webrtc.py`)

| Field | Type | Notes |
|-------|------|-------|
| `_buffer` | bytearray | Accumulates inbound `SpeechChunk` PCM until a full 20 ms frame is available. |
| `_queue` | asyncio.Queue | Paced 20 ms frames awaiting `recv()`. |
| `_timestamp` | int | Monotonic cumulative sample count (48 kHz units) for `pts` and pacing. |
| `_start` | float \| None | Wall-clock anchor for real-time pacing. |

**New operation — `flush()` / `stop_playback()`**: clears `_buffer` and `_queue` so the
current reply stops within one frame (~20 ms), well under the 500 ms barge-in budget (SC-008).
Resets nothing else; the track stays live for the caller's next turn.

---

## Per-call session state (`_CallSession`, `media/session.py`)

| Field | Type | Notes |
|-------|------|-------|
| `call_id` / `caller` / `bridge` | — | Existing. Isolation boundary keyed by `call_id`. |
| `_turn` | int | Existing turn counter. |
| `_speaking` | bool (new) | `True` while a reply is being played; gates barge-in — an interim caller segment during `_speaking` calls `bridge.stop_playback()`. |
| `_reprompted` | bool | Existing silence re-prompt latch (FR-014). |

**Lifecycle**: `connecting → welcome(turn 0) → [listening → thinking(stream) → speaking]* → ended`.
Barge-in transitions `speaking → listening` immediately on caller speech.

---

## Configuration additions (`src/app/core/config.py`)

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `tts_output_sample_rate` | int | 48000 | Cartesia output + outbound frame rate; matches Opus wire rate (§1). |
| `stt_endpointing_ms` | int | 800 | Silence (ms) before Deepgram finalizes a turn (`3, FR-005/FR-012). |
| `stt_utterance_end_ms` | int | 1000 | `UtteranceEnd` backstop for noisy lines (§3). |
| `provider_retry_attempts` | int | 1 | Extra silent retries before spoken apology (`5, FR-011). |
| `barge_in_enabled` | bool | True | Toggle for the barge-in behavior (FR-013). |
| `caller_silence_timeout_s` | float | 7.0 | Re-prompt window, aligned to clarified 5–8 s (FR-014). |

All carry safe defaults; none are secrets (constitution V). Document in `.env.example`.

---

## Persistent entities (unchanged)

`Call` and `CallEvent` (MongoDB, see `src/app/db/calls.py` and
`specs/003-voice-call-webhook/data-model.md`) are **not modified** by this feature. No new
migration.
