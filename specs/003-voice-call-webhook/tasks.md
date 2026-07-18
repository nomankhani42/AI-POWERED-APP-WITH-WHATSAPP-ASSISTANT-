---
description: "Task list for Meta Voice Call Webhook & Speech Services"
---

# Tasks: Meta Voice Call Webhook & Speech Services

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Input**: Design documents from `/specs/003-voice-call-webhook/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Contract + integration tests are included because the contracts and quickstart
explicitly define them. Keep each test file focused on one service/route.

**Modularity rule (per request + constitution Principle I)**: every task creates or edits a
**small, single-purpose file**. Do not write "complete feature" mega-files — a route file only
routes, a service file only wraps one provider, a db file only persists. Per-file soft cap ~120
lines; if a file grows past its one job, extract a helper rather than appending branches.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 = webhook, US2 = STT, US3 = TTS, US4 = automatic conversation loop (attended log, welcome, turn-taking)

## Path Conventions

Single async web-service; code under `src/app/`, tests under `tests/` (per plan.md structure).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies and configuration only — no feature logic here.

- [X] T001 Add runtime dependencies with `uv add deepgram-sdk cartesia aiortc httpx` (promote `httpx` from dev) and sync `uv.lock`
- [X] T002 [P] Add required secrets (no defaults, fail-fast) and defaulted tunables to `Settings` in src/app/core/config.py — `deepgram_api_key`, `cartesia_api_key`, `whatsapp_token`, `whatsapp_phone_id`, `whatsapp_verify_token`, `whatsapp_app_secret`, plus `deepgram_model`, `cartesia_model`, `cartesia_voice_id`, `stt_sample_rate`, `graph_api_version`
- [X] T003 [P] Add the new environment variables (names only, no values) to .env.example

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The one shared piece both speech stories depend on.

**⚠️ CRITICAL**: Complete before US2/US3 so their interfaces share the same types.

- [X] T004 Create the shared transient pipeline types `TranscriptSegment` and `SpeechChunk` (small dataclasses) in src/app/services/media/types.py

**Checkpoint**: Shared types ready — user stories can begin.

---

## Phase 3: User Story 1 - Meta call webhook (Priority: P1) 🎯 MVP

**Goal**: A verified webhook that authenticates, idempotently records, and advances the call
lifecycle (start → connected → ended), always returning 200 to Meta.

**Independent Test**: Run `tests/contract/test_calling_webhook.py` — verify handshake echoes the
challenge, a bad signature creates no record, a valid `connect` creates a `Call`+`CallEvent`, and a
duplicate event creates no second record.

### Tests for User Story 1

- [X] T005 [P] [US1] Contract test for webhook verify + signature reject + always-200 + idempotency in tests/contract/test_calling_webhook.py
- [X] T006 [P] [US1] Unit test for Call/CallEvent state transitions and monotonic status in tests/unit/test_call_records.py

### Implementation for User Story 1

- [X] T007 [P] [US1] Add `Call` and `CallEvent` Beanie documents (fields, enums, unique indexes) in src/app/db/documents.py
- [X] T008 [US1] Register `Call` and `CallEvent` in `init_beanie(...)` in src/app/db/mongo.py (depends on T007)
- [X] T009 [P] [US1] Create call-record CRUD + Redis idempotency helpers (`record_event`, `upsert_call`, `is_duplicate`) in src/app/db/calls.py
- [X] T010 [P] [US1] Create Meta Graph call actions (`pre_accept`/`accept`/`reject`/`terminate`) and `X-Hub-Signature-256` verification in src/app/services/meta_calling.py
- [X] T011 [US1] Create webhook route — `GET` verify handshake + `POST` events (signature-check, dedupe, upsert call/event, always return 200) in src/app/api/routes/whatsapp_calling.py (depends on T008, T009, T010)
- [X] T012 [US1] Register `whatsapp_calling.router` via `app.include_router(...)` in src/app/main.py (depends on T011)

**Checkpoint**: Webhook is live, verified, idempotent, and records the call lifecycle — MVP done.

---

## Phase 4: User Story 2 - Speech-to-text (Priority: P2)

**Goal**: A reusable Deepgram streaming (chunked) STT service that turns caller audio chunks into
transcript segments and handles silence and provider failure gracefully.

**Independent Test**: Run `tests/contract/test_stt_service.py` (Deepgram mocked) — clear audio →
matching final segments, silence → empty result (no error), outage → typed `SttError`.

### Tests for User Story 2

- [X] T013 [P] [US2] Contract test for STT streaming, silence handling, and provider-failure in tests/contract/test_stt_service.py

### Implementation for User Story 2

- [X] T014 [US2] Create the Deepgram streaming STT service — `transcribe_stream()` (async chunk-in → `TranscriptSegment`-out) plus `SttError`, wrapping `AsyncDeepgramClient.listen.v1.connect` (v1 chosen over v2/Flux to support the `nova-3` default + `send_finalize`; documented in module) — in src/app/services/stt.py (uses types from T004)

**Checkpoint**: STT service works standalone and is provider-swappable behind its interface.

---

## Phase 5: User Story 3 - Text-to-speech (Priority: P3)

**Goal**: A reusable Cartesia Sonic streaming TTS service that turns assistant reply text into
natural, low-latency audio chunks and handles empty input and provider failure gracefully.

**Independent Test**: Run `tests/contract/test_tts_service.py` (Cartesia mocked) — text → streamed
`SpeechChunk` audio, empty text → no audio (reported), outage → typed `TtsError`.

### Tests for User Story 3

- [X] T015 [P] [US3] Contract test for TTS streaming, empty-input handling, and provider-failure in tests/contract/test_tts_service.py

### Implementation for User Story 3

- [X] T016 [US3] Create the Cartesia Sonic streaming TTS service — `synthesize_stream()` (text-in → `SpeechChunk`-out) plus `TtsError`, wrapping the Cartesia TTS websocket context — in src/app/services/tts.py (uses types from T004)

**Checkpoint**: TTS service works standalone and is provider-swappable behind its interface.

---

## Phase 6: Polish & Cross-Cutting (Live media loop + validation)

**Purpose**: Wire the three stages into a live call. Each file stays single-purpose.

- [X] T017 [P] Create the WebRTC media bridge — `aiortc` peer/RTP, inbound Opus→PCM(16k) and outbound PCM→Opus — in src/app/services/media/webrtc.py
- [X] T018 Create the per-call pipeline session — the `STT → run_turn → TTS` loop, keyed per `call_id` for session isolation — in src/app/services/media/session.py (depends on T014, T016, T017)
- [X] T019 Wire `connect` → start media session (answer SDP via `meta_calling.accept`, non-blocking) and `terminate` → teardown, in src/app/api/routes/whatsapp_calling.py (depends on T011, T018)
- [X] T020 [P] Integration test — mocked media → STT → run_turn → TTS with two concurrent calls (no cross-talk) and per-stage failure handling, in tests/integration/test_call_pipeline.py
- [X] T021 [P] Document the new env vars in README (and confirm .env.example) 
- [X] T022 Run quickstart.md automated checks (feature test subset) + app-wiring/handshake smoke; live curl Scenarios 1–4 and real-call Scenario 5 require a running server + real Meta call (documented boundary)

---

## Phase 7: User Story 4 - Automatic conversation loop (Priority: P2)

**Goal**: When a call is attended, log it (caller number + `call_id`), auto-play a configurable
welcome as the opening turn, then run the listen → transcribe → `run_turn` → TTS loop turn-by-turn
with per-turn logging, and tear down cleanly on hangup. Extends the base media session from Phase 6.

**Independent Test**: Run `tests/integration/test_call_session.py` (media/STT/TTS/`run_turn` mocked) —
assert a `call_attended` log with the caller number, the welcome plays as turn 0 before any caller
audio, a 5-turn exchange logs one `ConversationTurn` per turn, a silent caller re-prompts then ends,
and a hangup cancels the loop cleanly (`Call.status == "ended"`, no audio after).

**Note**: Real-time chunk streaming (FR-017) and end-of-turn detection (FR-018) are already provided
by the Phase 4 STT service; US4 only orchestrates them plus welcome, logging, timeout, and teardown.

### Config for User Story 4

- [X] T023 [US4] Add defaulted tunables `welcome_message` (FR-022) and `caller_silence_timeout_s` to `Settings` in src/app/core/config.py
- [X] T024 [P] [US4] Add `WELCOME_MESSAGE` and `CALLER_SILENCE_TIMEOUT_S` (names + example values) to .env.example

### Tests for User Story 4

- [X] T025 [P] [US4] Integration test — attended log, auto-welcome opening turn, 5-turn loop, silence timeout, clean teardown — in tests/integration/test_call_session.py

### Implementation for User Story 4

- [X] T026 [P] [US4] Add `ConversationTurn` dataclass (`call_id`, `turn`, `transcript`, `reply`, `started_at`, `ended_at`) to src/app/services/media/types.py
- [X] T027 [P] [US4] Create observability helpers — `log_call_attended(call_id, wa_call_from)` (FR-015) and `log_turn(turn: ConversationTurn)` (FR-023), tokens never logged — in src/app/services/media/observability.py (uses T026)
- [X] T028 [US4] Emit the call-attended log on `connected` at session start via `log_call_attended(...)` in src/app/services/media/session.py (depends on T027)
- [X] T029 [US4] Add the auto-welcome opening turn — synthesize `settings.welcome_message` through `tts.synthesize_stream()` and play before listening (turn 0) — in src/app/services/media/session.py (depends on T023, T029 uses tts from T016)
- [X] T030 [US4] Log one `ConversationTurn` per exchange (welcome as turn 0, then each transcript+reply) via `log_turn(...)` in src/app/services/media/session.py (depends on T027, T029)
- [X] T031 [US4] Add caller-silence handling — after `caller_silence_timeout_s` re-prompt once, then gracefully `meta_calling.terminate(...)` — in src/app/services/media/session.py (depends on T023)
- [X] T032 [US4] Ensure clean teardown on `terminate`/hangup — cancel the loop task, close STT/TTS streams, finalize `Call` (`ended` + `ended_at`), process no audio after — in src/app/services/media/session.py (depends on T019)

**Checkpoint**: A caller is greeted automatically, holds a multi-turn spoken conversation, and every
attended call + turn is observable in the logs — User Story 4 complete.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: after Setup. T004 blocks US2 (T014) and US3 (T016).
- **User Stories (Phase 3–5)**: after Foundational.
  - US1 is fully independent of US2/US3 (does not need T004).
  - US2 and US3 are independent of each other; both only need T004.
- **Polish (Phase 6)**: after US1, US2, US3 (the media loop integrates all three).
- **User Story 4 (Phase 7)**: after Phase 6 — it extends the base media session (T018/T019) with
  attended logging, welcome, per-turn logging, silence timeout, and clean teardown.

### Within User Story 4

- Config (T023, T024) and shared pieces (T026 → T027) first. T025 test can be written up front.
- Then the session edits: T028, T029, T030, T031, T032 all edit `session.py` — run sequentially
  (same file). T024, T025, T026 are [P] with each other (different files).

### Within User Story 1

- Tests (T005, T006) first → documents (T007) → register (T008) → in parallel calls-db (T009) and meta_calling (T010) → route (T011) → wire into app (T012).

### Parallel Opportunities

- Setup: T002, T003 in parallel (T001 first — deps).
- US1: T005, T006 in parallel; then T007, T009, T010 in parallel (different files); T008/T011/T012 are sequential.
- US2 and US3 can be built fully in parallel by different people once T004 is done.
- Polish: T017, T020, T021 in parallel; T018 then T019 sequential.
- US4: T024, T025, T026 in parallel; T027 after T026; then T028→T032 sequential (all edit session.py).

---

## Parallel Example: User Story 1

```bash
# Tests first (parallel):
Task: "Contract test webhook in tests/contract/test_calling_webhook.py"
Task: "Unit test call records in tests/unit/test_call_records.py"

# Then independent modules (parallel):
Task: "Call/CallEvent documents in src/app/db/documents.py"
Task: "Call CRUD + idempotency in src/app/db/calls.py"
Task: "Meta Graph actions + signature verify in src/app/services/meta_calling.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 → **STOP & VALIDATE** the webhook
   independently (verify handshake, idempotency, lifecycle records). Deploy/demo.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 (webhook MVP) → test → demo.
3. US2 (STT) → test standalone → demo.
4. US3 (TTS) → test standalone → demo.
5. Phase 6 → wire the live media loop → run quickstart end-to-end.
6. US4 (Phase 7) → add attended log + auto-welcome + turn logging + silence/teardown → run
   `tests/integration/test_call_session.py` → demo a full automatic multi-turn conversation.

---

## Notes

- Keep files modular: route routes, service wraps one provider, db persists — no mega-files.
- [P] = different files, no dependency on incomplete tasks.
- Mock Deepgram/Cartesia/Meta/WebRTC at their module boundaries in tests — no live keys needed.
- Webhook must always return 200 to Meta; catch every exception inside the handler.
- Commit after each task or logical group; stop at any checkpoint to validate a story.
