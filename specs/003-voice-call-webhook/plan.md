# Implementation Plan: Meta Voice Call Webhook & Speech Services

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Branch**: `003-voice-call-webhook` | **Date**: 2026-07-05 (updated 2026-07-06) | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-voice-call-webhook/spec.md`

## Summary

Add a Meta (WhatsApp Business Calling) webhook that verifies the endpoint, authenticates and idempotently records the call lifecycle, and negotiates each call's media. Bridge the live call audio through two new reusable, provider-swappable speech services: **Deepgram** streaming (chunked) speech-to-text and **Cartesia Sonic** streaming text-to-speech, driven by the **OpenAI Agents SDK (gpt-4.1)** turn runner (`run_turn`), so callers hear a natural, low-latency, real-sounding voice.

When a call is attended (media established) the session (a) emits a **call-attended log entry** carrying the caller's number and `call_id` (FR-015), (b) automatically synthesizes and plays a **configurable welcome message** before listening — the opening turn (FR-016/FR-022), then (c) runs the continuous **listen → stream chunks → transcribe → `run_turn` → TTS → listen** loop turn-by-turn until the caller hangs up, logging each turn's transcript + reply for observability (FR-017–FR-021, FR-023). The webhook and both speech services remain independently testable stages, wired into the existing FastAPI app and the existing Agents-SDK turn runner that already produces the assistant's text replies.

## Technical Context

**Language/Version**: Python 3.12+ (managed by `uv`), matching the existing service.

**Primary Dependencies**: FastAPI + Uvicorn (existing); `openai-agents` turn runner (existing, reused); `deepgram-sdk` (async streaming STT, chunked); `cartesia` (async Sonic streaming TTS); `httpx` (Meta Graph API calls — promote from dev to runtime dep); `aiortc` (WebRTC/RTP media termination for WhatsApp Business Calling); `redis` (existing, in-flight call state); `beanie`/`pymongo` (existing, durable call records).

**Storage**: MongoDB via Beanie for durable `Call` + `CallEvent` records (Principle III long-term). Redis for in-flight per-call session state and event-idempotency keys (Principle III short-term).

**Testing**: pytest + pytest-asyncio + httpx TestClient (existing setup); `fakeredis` for session/idempotency tests; Deepgram, Cartesia, Meta Graph, and the WebRTC media layer mocked at their module boundaries (Principle IV: each stage isolated behind a stable interface).

**Target Platform**: Linux server (Uvicorn), publicly reachable HTTPS webhook.

**Project Type**: Single async web-service (backend only) — extends `src/app/`.

**Performance Goals**: Conversational turn latency perceived as responsive — first synthesized audio begins playing within ~1.5 s of the caller finishing an utterance (SC-005). STT emits interim/finalized results as chunks stream in; TTS streams audio out as text arrives from the agent so playback starts before the full reply is generated. The welcome message begins playing effectively immediately on connect, before any caller speech (SC-010). A single call sustains at least 5 back-and-forth turns without per-turn latency growth (SC-011).

**Constraints**: Webhook MUST always return 200 to Meta and acknowledge within Meta's retry window (skill hard-rule #1, FR-004). No blocking I/O on the event/media path (Principle II). Secrets env-only, fail-fast (Principle V). Concurrent calls fully isolated (FR-013).

**Scale/Scope**: Small-team support line — tens of concurrent calls initially; design must not preclude horizontal scaling (stateless webhook, Redis-shared call state).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this plan complies |
|-----------|--------|------------------------|
| I. Modular Architecture | ✅ PASS | New concerns each get their own module: `api/routes/whatsapp_calling.py` (transport/verify), `services/meta_calling.py` (Graph call actions), `services/media/` (WebRTC bridge), `services/stt.py` (Deepgram), `services/tts.py` (Cartesia), `db/calls.py` + `db/documents.py` (records). No mixing of transport, logic, and I/O in one file; per-file focus kept small. |
| II. Async-First FastAPI | ✅ PASS | All new I/O uses async clients: `AsyncDeepgramClient`, async Cartesia websocket, `httpx.AsyncClient`, `aiortc`, `redis.asyncio`, Beanie. No blocking calls on the request/media path. |
| III. Layered Memory | ✅ PASS | Redis holds in-flight call/turn state + idempotency keys; MongoDB holds durable `Call`/`CallEvent` history. Each store accessed only through its dedicated module. |
| IV. Voice Pipeline Integrity | ✅ PASS | Exactly the mandated contract: audio in → Deepgram STT → Agents SDK (`run_turn`, gpt-4.1) → Cartesia Sonic TTS → audio out, over Meta transport. Each stage is an isolated component behind a stable interface (swappable provider) with explicit timeout/fallback error handling (FR-009). |
| V. Configuration & Secrets | ✅ PASS | New required secrets added to the typed `Settings` with **no default** (fail-fast): `deepgram_api_key`, `cartesia_api_key`, `whatsapp_token`, `whatsapp_phone_id`, `whatsapp_verify_token`, `whatsapp_app_secret`. Defaulted tunables include the configurable `welcome_message` (FR-022). Documented in `.env.example` in the same change. Secrets never logged (the call-attended log records only caller number + `call_id`, never tokens). |
| VI. Documentation-Driven Dev | ✅ PASS | Deepgram streaming and Cartesia Sonic APIs verified via Context7 during Phase 0; Meta WhatsApp webhook verify/signature pattern taken from the `meta-whatsapp-api` skill. FastAPI/WhatsApp specialist skills used for their domains. |

**Gate result: PASS** (initial). Re-evaluated post-design below.

### Post-Design Re-check

Design keeps every stage behind a mockable module boundary; no principle is violated. One dependency (`aiortc`) is added beyond the constitution's named stack and is recorded in Complexity Tracking with justification. **Gate result: PASS.**

## Project Structure

### Documentation (this feature)

```text
specs/003-voice-call-webhook/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── whatsapp-calling-webhook.md
│   ├── call-session-loop.md   # attended log, welcome, turn loop (US4)
│   ├── stt-service.md
│   └── tts-service.md
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/app/
├── main.py                        # register whatsapp_calling.router (edit)
├── core/
│   └── config.py                  # add speech + Meta secrets (edit)
├── api/routes/
│   └── whatsapp_calling.py        # NEW: GET verify + POST webhook (always 200)
├── services/
│   ├── meta_calling.py            # NEW: Graph API call actions (pre_accept/accept/reject/terminate) + signature verify
│   ├── stt.py                     # NEW: Deepgram streaming (chunked) STT service
│   ├── tts.py                     # NEW: Cartesia Sonic streaming TTS service
│   └── media/
│       ├── __init__.py
│       ├── session.py             # NEW: per-call orchestration — attended log (FR-015),
│       │                          #      auto welcome (FR-016), listen→STT→run_turn→TTS
│       │                          #      loop with per-turn logging (FR-017–021,023), teardown
│       └── webrtc.py              # NEW: aiortc peer/RTP bridge (Opus in/out ↔ PCM)
├── db/
│   ├── documents.py               # add Call, CallEvent Beanie documents (edit)
│   ├── calls.py                   # NEW: durable call record CRUD + idempotency helpers
│   └── mongo.py                   # register new documents in init_beanie (edit)
└── agent/
    └── service.py                 # reused as-is (run_turn produces reply text)

tests/
├── contract/
│   ├── test_calling_webhook.py    # verify handshake, signature reject, 200-always, idempotency
│   ├── test_stt_service.py        # streaming transcript, silence, provider-failure
│   └── test_tts_service.py        # streaming audio, empty-input, provider-failure
├── integration/
│   ├── test_call_pipeline.py      # mocked media → STT → run_turn → TTS loop, session isolation
│   └── test_call_session.py       # attended log, auto-welcome opening turn, multi-turn loop,
│                                  #   clean teardown on hangup (US4: FR-015–021,023)
└── unit/
    └── test_call_records.py       # Call/CallEvent state transitions + idempotency
```

**Structure Decision**: Single async web-service. This feature extends the existing `src/app/` layout (FastAPI app factory in `main.py`, routes under `api/routes/`, integrations under `services/`, persistence under `db/`). It adds one route module, three service areas (Meta calling, STT, TTS) plus a small media sub-package, and two Beanie documents — no new top-level project. The existing `run_turn` agent orchestration is reused unchanged so the agent layer stays the single source of reply text.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Added dependency `aiortc` (beyond the constitution's named stack) | WhatsApp Business Calling delivers/receives live call audio as Opus RTP negotiated over WebRTC (SDP offer/answer via the webhook). Terminating that media in-process to feed Deepgram and play back Cartesia audio requires a WebRTC/RTP stack; `aiortc` is the standard async Python option. | A pure-`httpx` approach cannot carry real-time RTP media — the Graph API only conveys call *control* + SDP. Hosting media on an external SFU/telephony bridge (e.g. a third-party media server) adds an entire extra service and latency hop, contradicting YAGNI and the low-latency goal (SC-005). `aiortc` is the smallest option that satisfies Principle IV's audio-in/audio-out contract in-process. |
