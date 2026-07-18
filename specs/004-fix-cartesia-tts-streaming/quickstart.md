# Quickstart & Validation: Clear Cartesia Voice Playback & Real-Time Streaming Loop

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Feature**: 004-fix-cartesia-tts-streaming | **Date**: 2026-07-07

How to run and prove this feature works end to end. Details live in the contracts and
data-model; this is the run/validate guide only.

## Prerequisites

- `uv` installed; dependencies synced: `uv sync`
- `.env` populated (see `.env.example`) with the existing secrets plus the new tunables:
  - `OPENAI_API_KEY`, `MONGODB_URI`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`,
    `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`
  - New (optional, safe defaults): `TTS_OUTPUT_SAMPLE_RATE=48000`,
    `STT_ENDPOINTING_MS=800`, `STT_UTTERANCE_END_MS=1000`, `PROVIDER_RETRY_ATTEMPTS=1`,
    `BARGE_IN_ENABLED=true`, `CALLER_SILENCE_TIMEOUT_S=7`, `CARTESIA_VOICE_ID=<a real voice id>`
- Redis and MongoDB reachable.

## Run

```bash
uv run uvicorn app.main:app --reload --port 8000
```

## Automated validation (run first — no phone needed)

```bash
uv run pytest tests/unit tests/integration -q
```

Expected: green. Key checks that must pass (see contracts for full acceptance lists):

| Scenario | Proves | Spec |
|----------|--------|------|
| Outbound framing: every encoder frame is 960 samples @ 48 kHz, monotonic `pts`, lossless reconstruction | Distortion fixed | FR-001, FR-002, SC-001/2 |
| `stop_playback()` empties the queue mid-reply; next reply starts clean | Barge-in stop < 500 ms | FR-013, SC-008 |
| STT with ~0.8 s trailing pause → one final segment; < 0.8 s pause → no premature final | Endpointing | FR-005, FR-012 |
| `run_turn_stream` yields deltas in order; join == full reply | Streamed reply | FR-007 |
| `synthesize_stream` yields first `SpeechChunk` before last text piece pushed | Early TTS | FR-008, SC-005 |
| Provider fails twice → one retry, then apology spoken, loop continues | Failure handling | FR-011 |
| Silence after welcome → one re-prompt, then graceful terminate | No-input | FR-014 |

## Manual end-to-end validation (live call)

1. Expose the webhook (e.g. tunnel) and place a WhatsApp call to the business number.
2. **US1 / P1 — clear audio**: On connect, the welcome message MUST sound continuous and
   natural — **no buzzing, stutter, or "old TV" static**. Listen to a full multi-sentence
   reply; it stays clean throughout. *(SC-001, SC-003)*
3. **US2 / P2 — clear listening**: After the welcome, speak a request. It is captured and
   transcribed accurately; the turn finalizes ~0.8 s after you stop, and a brief mid-sentence
   pause does NOT cut you off. *(SC-004, SC-007)*
4. **US3 / P3 — streaming reply**: Ask something with a multi-sentence answer. The assistant
   starts speaking < ~1.5 s after you stop — before the whole answer is formed — and the audio
   is continuous and in order. *(SC-005)*
5. **Barge-in (FR-013)**: While the assistant is speaking, start talking. Playback stops
   within ~0.5 s and the system captures your new speech. *(SC-008)*
6. **Loop (FR-010)**: After each reply, it returns to listening; complete several turns with
   no turn hanging in silence. *(SC-006)*
7. **Failure path (FR-011)** *(optional, forced)*: temporarily point `CARTESIA_API_KEY` at an
   invalid value for one turn → hear the retry then a brief spoken apology, call continues.

## What "done" looks like

All automated checks green **and** manual steps 2–6 observed on a live call, matching the
Success Criteria in [spec.md](./spec.md). The `/speckit-tasks` command will turn this plan
into the ordered task list.
