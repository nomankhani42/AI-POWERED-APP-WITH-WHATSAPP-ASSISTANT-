# Implementation Plan: Clear Cartesia Voice Playback & Real-Time Streaming Loop

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Branch**: `004-fix-cartesia-tts-streaming` | **Date**: 2026-07-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-fix-cartesia-tts-streaming/spec.md`

## Summary

Fix the garbled "old TV / toun toush" playback callers hear today and complete the real-time
conversation loop. Root cause of the distortion (see [research.md](./research.md) `1): the
outbound WebRTC track (`_OutboundTrack` in `src/app/services/media/webrtc.py`) hands each
arbitrary-length Cartesia PCM chunk to aiortc's Opus encoder as a single `av.AudioFrame`,
so the encoder receives irregular, non-20 ms frames and emits stuttering/garbled Opus. The
fix reframes outbound audio into fixed **20 ms mono frames at 48 kHz** (resampled once)
with monotonic timestamps before it reaches the encoder.

On top of that, three streaming behaviors are wired through the existing
Deepgram → OpenAI Agents SDK → Cartesia Sonic → Meta pipeline: (P2) tune Deepgram
**silence-based endpointing** to ~0.8 s so a caller's turn finalizes on a natural pause;
(P3) switch the agent turn to **`Runner.run_streamed`** and pipe its token deltas straight
into `synthesize_stream`, so TTS starts on the first ready portion; plus **barge-in**
(stop playback the instant the caller speaks) and a **retry-once + spoken-apology**
provider-failure path. No new external services; all work is inside `src/app`.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI + Uvicorn; `aiortc` / `av` (WebRTC media bridge, Opus);
`openai-agents` SDK (GPT-4.1); `cartesia==3.3.0` (Sonic TTS); `deepgram-sdk==7.4.0`
(streaming STT); `redis` (async session); `motor` (MongoDB); `pydantic-settings`.

**Storage**: MongoDB for durable `Call` / `CallEvent` records (unchanged); Redis for
short-term agent session state (unchanged). This feature persists no new durable data.

**Testing**: pytest (async) under `tests/`; provider SDKs mocked at their module boundary
(`stt.transcribe_stream`, `tts.synthesize_stream`, `MediaBridge`).

**Target Platform**: Linux server (async FastAPI voice backend).

**Project Type**: Single async web-service (existing `src/app` layout).

**Performance Goals**: reply playback starts < 1.5 s after caller stops (SC-005); barge-in
stops playback < 500 ms after caller speech detected (SC-008); ≥ 95% of replies rated fully
intelligible with no artifacts (SC-001).

**Constraints**: Real-time, non-blocking async only (constitution II). Outbound audio MUST
be framed to match the negotiated Opus wire format (48 kHz) in fixed 20 ms frames — this is
the audio-format reconciliation the spec calls out. Playback ordering MUST stay monotonic.

**Scale/Scope**: Per-call isolated `_CallSession` objects (module registry keyed by
`call_id`); multiple concurrent calls. Scope is a fix + streaming completion of the existing
003 pipeline, not a rewrite.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Modular Architecture | PASS | Each fix stays in its existing single-purpose module: framing in `media/webrtc.py`, endpointing in `services/stt.py`, streaming turn in `agent/service.py`, loop wiring/barge-in/retry in `media/session.py`. No module mixes new concerns. |
| II. Async-First FastAPI Service | PASS | All touched paths are `async`; no blocking calls introduced. Reframing/resampling uses `av` in-memory (µs-scale, non-blocking). `uv` remains the toolchain. |
| III. Layered Memory | PASS | No memory-layer change; Redis session and Mongo durability untouched. |
| IV. Voice Pipeline Integrity | PASS | Fixed contract preserved (Deepgram → Agents SDK GPT-4.1 → Cartesia Sonic → Meta). Each stage stays behind its stable interface; this feature strengthens stage boundaries and adds explicit retry/fallback (never silent drops). |
| V. Configuration & Secrets Discipline | PASS | New tunables (`stt_endpointing_ms`, `tts_output_sample_rate`, `provider_retry_attempts`) added to the single typed `Settings` with safe defaults; no secrets added/logged. Env example updated in the same change. |
| VI. Documentation-Driven Development | PASS | Agents SDK streaming (`Runner.run_streamed` + `ResponseTextDeltaEvent`) and Deepgram `endpointing`/`utterance_end_ms` verified via Context7 before planning (see research.md). |

**Result**: PASS — no violations. Complexity Tracking table intentionally empty.

## Project Structure

### Documentation (this feature)

```text
specs/004-fix-cartesia-tts-streaming/
├── plan.md              # This file
├── research.md          # Phase 0 — root-cause + decisions
├── data-model.md        # Phase 1 — transient types & state
├── quickstart.md        # Phase 1 — validation guide
├── contracts/           # Phase 1 — updated service contracts
│   ├── media-bridge.md
│   ├── stt-service.md
│   ├── tts-service.md
│   └── agent-turn.md
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/app/
├── core/
│   └── config.py               # + stt_endpointing_ms, tts_output_sample_rate, provider_retry_attempts, barge_in_enabled
├── services/
│   ├── stt.py                  # tune endpointing (~0.8s), utterance_end_ms/vad_events; expose interim vs final
│   ├── tts.py                  # output at 48 kHz to match Opus wire rate; accept streamed text (already does)
│   └── media/
│       ├── webrtc.py           # CORE FIX: fixed 20 ms / 48 kHz outbound framing + flush() for barge-in
│       ├── session.py          # stream agent reply → TTS; barge-in stop; retry-once + spoken apology
│       ├── types.py            # (unchanged) SpeechChunk / TranscriptSegment / ConversationTurn
│       └── observability.py    # (unchanged)
└── agent/
    └── service.py              # + run_turn_stream(): Runner.run_streamed → async text-delta generator

tests/
├── unit/                       # framing, endpointing config, retry logic
└── integration/                # end-to-end loop with mocked providers (barge-in, streaming, silence)
```

**Structure Decision**: Reuse the existing single-service `src/app` layout from feature 003.
This feature edits five existing modules and adds one function (`run_turn_stream`); it
introduces no new package or top-level directory, consistent with the in-place modularity
preference and constitution Principle I.

## Complexity Tracking

> No constitution violations. Table intentionally empty.
