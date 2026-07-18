---
description: "Task list for Clear Cartesia Voice Playback & Real-Time Streaming Loop"
---

# Tasks: Clear Cartesia Voice Playback & Real-Time Streaming Loop

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Input**: Design documents from `/specs/004-fix-cartesia-tts-streaming/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — the repo already follows a `tests/{unit,integration,contract}` TDD
pattern (feature 003) and quickstart.md defines an automated validation table. Write each
test before its implementation task and confirm it fails first.

**Organization**: Grouped by user story (P1→P3) plus a robustness phase for the cross-cutting
FRs (barge-in, provider failure, no-input). Tasks are intentionally small and single-file.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different file, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 for story phases; no label for Setup / Foundational / Robustness / Polish

## Path Conventions

Single async service — existing `src/app/` layout, `tests/` at repo root.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the workspace is ready; no new deps required (all SDKs already installed).

- [x] T001 Run `uv sync` and confirm `aiortc`, `av`, `openai-agents`, `cartesia`, `deepgram-sdk` import cleanly in the venv

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: New configuration every story reads. **MUST complete before any story phase.**

**⚠️ CRITICAL**: STT, TTS, and session tasks all depend on these settings.

- [x] T002 Add `tts_output_sample_rate: int = 48000` and `stt_endpointing_ms: int = 800` to `Settings` in src/app/core/config.py
- [x] T003 Add `stt_utterance_end_ms: int = 1000`, `provider_retry_attempts: int = 1`, `barge_in_enabled: bool = True` to `Settings` in src/app/core/config.py
- [x] T004 Lower `caller_silence_timeout_s` default to `7.0` (clarified 5–8 s window) in src/app/core/config.py
- [x] T005 [P] Document the six new/changed tunables in .env.example

**Checkpoint**: Config available — story phases can begin.

---

## Phase 3: User Story 1 - Caller hears clear, natural spoken replies (Priority: P1) 🎯 MVP

**Goal**: Eliminate the "old TV / toun toush" distortion by handing the Opus encoder only
fixed 20 ms / 48 kHz frames (research.md §1). This is the highest-value fix and unblocks live
validation of everything else.

**Independent Test**: On a live call the welcome and any reply sound continuous and natural —
no buzzing/stutter/static (SC-001, SC-003); unit test proves every encoder frame is 960
samples @ 48 kHz with monotonic `pts` and lossless reconstruction.

### Tests for User Story 1

- [x] T006 [P] [US1] Unit test: `_OutboundTrack` emits only 960-sample/48 kHz frames with strictly increasing `pts` and reconstructs input PCM losslessly, in tests/unit/test_outbound_framing.py

### Implementation for User Story 1

- [x] T007 [US1] Set `output_format.sample_rate = settings.tts_output_sample_rate` (48000) and emit `SpeechChunk.sample_rate` accordingly in src/app/services/tts.py
- [x] T008 [US1] Update the TTS contract test to expect `sample_rate == 48000` on every `SpeechChunk` in tests/contract/test_tts_service.py
- [x] T009 [US1] Add a persistent `av.AudioResampler(format="s16", layout="mono", rate=48000)` and a `bytearray` buffer to `_OutboundTrack.__init__` in src/app/services/media/webrtc.py
- [x] T010 [US1] In `_OutboundTrack.push`/framing, resample incoming PCM and accumulate into the buffer, slicing exactly 960-sample (1920-byte) frames in src/app/services/media/webrtc.py
- [x] T011 [US1] Rewrite `_OutboundTrack.recv()` to return one buffered 20 ms frame at 48 kHz with monotonic `pts` and real-time pacing in src/app/services/media/webrtc.py
- [x] T012 [US1] Retain sub-frame leftover PCM between chunks and zero-pad only the final partial frame at end-of-reply in src/app/services/media/webrtc.py

**Checkpoint**: Playback is clean and continuous — MVP is demonstrable on a live call.

---

## Phase 4: User Story 2 - Caller speech captured clearly and understood (Priority: P2)

**Goal**: Stream caller audio to Deepgram in chunks and finalize a turn on a ~0.8 s pause
(silence-based endpointing), so brief mid-sentence pauses don't cut the caller off
(research.md §3).

**Independent Test**: Audio with a ~0.8 s trailing pause yields exactly one final segment; a
< 0.8 s pause yields none prematurely; non-empty interim segments are emitted (SC-004, SC-007,
FR-012).

### Tests for User Story 2

- [x] T013 [P] [US2] Contract test: `transcribe_stream` finalizes once after a ~0.8 s pause and does NOT finalize on a shorter pause, in tests/contract/test_stt_service.py
- [x] T014 [P] [US2] Contract test: non-empty interim (`is_final=False`) segments are emitted before the final, in tests/contract/test_stt_service.py

### Implementation for User Story 2

- [x] T015 [US2] Add `endpointing=settings.stt_endpointing_ms`, `utterance_end_ms=str(settings.stt_utterance_end_ms)`, `vad_events=True`, `channels=1` to the `listen.v1.connect(...)` call in src/app/services/stt.py
- [x] T016 [US2] Ensure `_on_message` emits non-empty interim segments (not just finals) so barge-in can consume them, in src/app/services/stt.py
- [x] T017 [US2] Verify the `inbound_pcm()` → `transcribe_stream` chunked path in `_conversation_loop` needs no change and add a brief comment referencing endpointing, in src/app/services/media/session.py

**Checkpoint**: Caller speech is captured chunked and turns finalize on a natural pause.

---

## Phase 5: User Story 3 - Replies begin playing while the agent is still responding (Priority: P3)

**Goal**: Stream the agent's reply token-by-token and pipe it straight into Cartesia so
playback starts on the first ready portion (< 1.5 s), instead of waiting for the whole answer
(research.md §4).

**Independent Test**: `run_turn_stream` yields ordered deltas whose join equals the full
reply; `synthesize_stream` yields its first `SpeechChunk` before the last text piece is pushed
(SC-005, FR-007, FR-008).

### Tests for User Story 3

- [x] T018 [P] [US3] Unit test: `run_turn_stream` yields deltas in order and joins to the full reply, ignoring non-text events (mock `Runner.run_streamed`), in tests/unit/test_agent_stream.py
- [x] T019 [P] [US3] Contract test: `synthesize_stream` yields the first chunk before the final streamed text piece is pushed, in tests/contract/test_tts_service.py

### Implementation for User Story 3

- [x] T020 [US3] Add `run_turn_stream(message, phone_number, conversation_id) -> AsyncIterator[str]` using `Runner.run_streamed` + `ResponseTextDeltaEvent`, consuming the stream to completion, in src/app/agent/service.py
- [x] T021 [US3] In `_handle_turn`, replace `run_turn` + `_speak(reply)` with `run_turn_stream` piped into `synthesize_stream`, while tee-ing deltas to assemble the full reply text, in src/app/services/media/session.py
- [x] T022 [US3] Keep `ConversationTurn.reply` = assembled full reply for `log_turn`, and preserve the empty-reply `_AGENT_FALLBACK` behavior, in src/app/services/media/session.py

**Checkpoint**: Replies start speaking early and the listen→respond loop feels live.

---

## Phase 6: Conversation Robustness (Cross-Cutting FRs)

**Purpose**: Behaviors that span the stories — barge-in (FR-013), provider failure (FR-011),
no-input (FR-014). Depends on US1 (playback) and US2 (interim STT signal).

### Barge-in (FR-013, SC-008)

- [x] T023 [P] Add `flush()` to `_OutboundTrack` (drain queue + clear buffer) and `stop_playback()` to `MediaBridge` in src/app/services/media/webrtc.py
- [x] T024 Add a `_speaking` flag and, when `barge_in_enabled`, call `bridge.stop_playback()` on the first non-empty interim caller segment received during playback, in src/app/services/media/session.py
- [x] T025 [P] Integration test: an interim caller segment during a reply flushes playback within budget and resumes listening, in tests/integration/test_call_session.py

### Provider failure (FR-011)

- [x] T026 Add a retry-once helper (honoring `provider_retry_attempts`) wrapping the STT/TTS calls in src/app/services/media/session.py
- [x] T027 On repeated STT/TTS failure, speak the apology ("Sorry, I didn't catch that…") and continue the loop instead of tearing down the call, in src/app/services/media/session.py
- [x] T028 [P] Integration test: provider fails then succeeds on retry, and a persistent failure triggers apology + loop continuation, in tests/integration/test_call_pipeline.py

### No-input (FR-014)

- [x] T029 Verify the re-prompt-once-then-`_terminate` path uses the new `caller_silence_timeout_s` default and matches the clarified 5–8 s window, in src/app/services/media/session.py

**Checkpoint**: Barge-in, failure recovery, and silence handling all behave per the clarifications.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T030 [P] Update README voice section with the new tunables and behavior (endpointing, streaming, barge-in)
- [x] T031 Run `uv run pytest tests/unit tests/integration tests/contract -q` and fix any regressions
- [ ] T032 Execute the live-call manual validation (quickstart.md steps 2–6) and confirm SC-001…SC-008

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: after Setup — **blocks all stories** (config).
- **US1 (Phase 3)**: after Foundational. No dependency on other stories. **MVP.**
- **US2 (Phase 4)**: after Foundational. Independent of US1.
- **US3 (Phase 5)**: after Foundational. Independent, but best validated after US1 (needs clean audio to hear it).
- **Robustness (Phase 6)**: after US1 (needs `stop_playback`) and US2 (needs interim segments).
- **Polish (Phase 7)**: after all desired phases.

### Key within-file sequences (NOT parallel)

- src/app/core/config.py: T002 → T003 → T004 (same file).
- src/app/services/media/webrtc.py: T009 → T010 → T011 → T012, then T023 (same file).
- src/app/services/media/session.py: T021 → T022 → T024 → T026 → T027 → T029 (same file).
- src/app/services/tts.py: T007 before US3's synthesize streaming assumptions.

### Parallel Opportunities

- T005 (env docs) runs alongside T002–T004.
- Test tasks T006, T013, T014, T018, T019 are all `[P]` (distinct test files/areas) and can be written first, in parallel.
- Across stories after Foundational: US1 (webrtc/tts), US2 (stt), and US3 (agent) touch mostly different files and can progress in parallel by different developers, converging in Phase 6.

---

## Parallel Example: User Story 1

```bash
# Write the failing test first:
Task: "Unit test outbound framing in tests/unit/test_outbound_framing.py"  # T006

# Then implementation in tts.py and webrtc.py (webrtc tasks are sequential — same file):
Task: "Set Cartesia output to 48000 in src/app/services/tts.py"            # T007 [P vs webrtc]
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational (config).
2. Phase 3 US1 — the outbound-framing fix.
3. **STOP and VALIDATE**: place a live call; confirm the welcome + a reply sound clean (no "old TV" buzz). This alone is a shippable, demonstrable win.

### Incremental Delivery

1. Foundation ready → US1 (clear audio, MVP) → demo.
2. US2 (chunked listening + endpointing) → demo.
3. US3 (streaming reply / early TTS) → demo.
4. Phase 6 robustness (barge-in, failure, silence) → demo.
5. Phase 7 polish + full validation.

---

## Notes

- [P] = different file, no dependency on an incomplete task.
- Verify each test fails before implementing it.
- Commit after each task or logical group.
- The single biggest lever is US1 (T009–T012): fix the framing first; everything else is only
  audible once playback is clean.
