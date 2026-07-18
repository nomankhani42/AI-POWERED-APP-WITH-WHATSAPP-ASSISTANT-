---
description: "Task list for Fix Voice Call Flow"
---

# Tasks: Fix Voice Call Flow (Welcome, Turn-Taking, Tool Fillers & Logging)

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Input**: Design documents from `/specs/005-fix-voice-call-flow/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — the quickstart validation guide enumerates specific tests and the success
criteria (SC-001…SC-007) are proven by them; regression safety for features 003/004 matters.

**Organization**: Grouped by user story. All of US1/US2/US3 are P1; US4 is P2. This is a fix to one
existing loop, so US3 builds on US2's turn refactor (noted in Dependencies).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 / US4 (Setup/Foundational/Polish carry no story label)
- Exact file paths are included in every task.

## Path Conventions

Single async web service. Source under `src/app/`, tests under `tests/` (`pytest`, `asyncio_mode=auto`).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Configuration the fillers depend on.

- [X] T001 Add filler-phrase settings (`filler_generic`, `filler_check_availability`, `filler_book_room`, `filler_cancel_booking`, `filler_list_bookings`, each with the default from data-model.md) to `Settings` in src/app/core/config.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared building blocks used by every story. MUST complete before Phase 3+.

- [X] T002 [P] Add `AgentStreamEvent` dataclass with a `kind` discriminator (`text_delta` | `tool_call` | `tool_output`) and fields `text`, `tool_name`, `ok` to src/app/services/media/types.py (per data-model.md)
- [X] T003 Add `run_turn_events()` to src/app/agent/service.py mapping `Runner.run_streamed().stream_events()` to `AgentStreamEvent` (text delta / `tool_call_item` / `tool_call_output_item`, tool name via `getattr(item.raw_item, "name", None)`) per contracts/agent-turn-events.md
- [X] T004 Reimplement `run_turn_stream()` in src/app/agent/service.py as a text-only view over `run_turn_events()` (yield `e.text` for `text_delta`), keeping the WhatsApp/chat path behavior unchanged
- [X] T005 [P] Create `filler_for(tool_name)` in src/app/services/media/fillers.py resolving each known tool → its settings phrase and unknown/`None` → `filler_generic` (never empty) per contracts/filler-phrases.md
- [X] T006 [P] Add the new logging helpers (`log_welcome`, `log_tool_call`, `log_tool_result`, `log_filler`, `log_playback`, `log_barge_in`, `log_reprompt`, `log_fallback`, `log_call_ended`) to src/app/services/media/observability.py at INFO with `call_id` in message + `extra`, per contracts/call-observability-log.md

**Checkpoint**: Structured turn events, filler resolver, and log helpers exist and import cleanly.

---

## Phase 3: User Story 1 — Reliable greeting then hand-off (Priority: P1)

**Goal**: Welcome always plays in full, is non-interruptible, then the loop starts listening.

**Independent Test**: Accept a call; caller hears the full welcome before listening; caller audio
during the welcome is discarded (FR-020, SC-001).

- [X] T007 [US1] Wire `log_welcome(call_id)` into `_play_welcome` in src/app/services/media/session.py and confirm `_play_welcome` is awaited before `_conversation_loop` starts (welcome completes before any listening)
- [X] T008 [US1] Confirm the welcome uses the non-barge-in `_speak` path and that no STT listening runs during the welcome in src/app/services/media/session.py (barge-in scoped to replies only, FR-020)
- [X] T009 [P] [US1] Add integration test tests/integration/test_session_welcome.py: welcome plays to completion and caller audio during it does not interrupt or start a turn

**Checkpoint**: US1 independently testable.

---

## Phase 4: User Story 2 — Caller speaks and hears a spoken reply (Priority: P1)

**Goal**: Every finished caller utterance yields an audible reply (or a spoken fallback), streamed to TTS.

**Independent Test**: After the welcome, a caller question produces a spoken reply, then the loop
listens again; an empty reply degrades to a spoken fallback (SC-002).

- [X] T010 [US2] Refactor `_handle_turn` in src/app/services/media/session.py to consume `run_turn_events(...)`, feeding `text_delta` pieces into `synthesize_stream` for the reply (preserve barge-in via `_play_with_barge_in` and the collected-reply/fallback logic)
- [X] T011 [US2] Wire `log_playback(call_id, turn, "start"/"stop")` around reply playback and `log_barge_in(call_id, turn)` on interruption in src/app/services/media/session.py
- [X] T012 [P] [US2] Add integration test tests/integration/test_session_reply.py: a `text_delta`-only turn is spoken back; an empty-reply turn speaks the fallback (never silence)

**Checkpoint**: US2 independently testable; text conversation path unchanged from 004 behavior.

---

## Phase 5: User Story 3 — Spoken "let me check" filler while a tool runs (Priority: P1)

**Goal**: On a tool call, speak the tool-tailored filler before the answer; none on no-tool turns.

**Independent Test**: A tool-triggering turn speaks the tailored filler before the answer; a no-tool
turn speaks no filler; multiple tools each get their own filler (SC-003, SC-004).

- [X] T013 [US3] In `_handle_turn` in src/app/services/media/session.py, on each `tool_call` event speak `filler_for(event.tool_name)` via `_speak` before the reply text is played (one filler per tool call)
- [X] T014 [US3] Wire `log_tool_call`, `log_filler`, and `log_tool_result(ok=…)` around the `tool_call`/`tool_output` events in src/app/services/media/session.py
- [X] T015 [P] [US3] Add unit test tests/unit/test_fillers.py: each known tool name → its tailored phrase; unknown/`None` → non-empty generic
- [X] T016 [P] [US3] Add integration test tests/integration/test_session_tool_filler.py: `tool_call` → filler spoken before reply; no-tool turn → no filler; two tools in one turn → filler for each

**Checkpoint**: US3 independently testable.

---

## Phase 6: User Story 4 — Full call flow visible in backend logs (Priority: P2)

**Goal**: Every milestone logged at INFO, correlated by `call_id`, reconstructable as one timeline,
with no secrets; per-call isolation under concurrency.

**Independent Test**: A test call with a lookup yields the full ordered log timeline tied to one
`call_id`; two concurrent calls stay separable; no token/secret/tool-arg appears (SC-005–007).

- [X] T017 [US4] Wire `log_reprompt`, `log_fallback`, and `log_call_ended(reason)` into the silence re-prompt, apology/fallback, and teardown paths (`_conversation_loop`, `_speak` fallback callers, `_run` finally / `_terminate`) in src/app/services/media/session.py
- [X] T018 [P] [US4] Add unit test tests/unit/test_observability.py: each helper emits `call_id` (message + `extra`) and no record contains a token/secret/tool-argument value
- [X] T019 [P] [US4] Add integration test tests/integration/test_session_logging.py: assert the full ordered timeline for a lookup turn and that two concurrent fake calls produce separable, correctly-attributed records

**Checkpoint**: US4 independently testable; full observability complete.

---

## Phase 7: Polish & Cross-Cutting

- [X] T020 [P] Document the new `FILLER_*` environment variables in .env.example (per constitution Development Workflow)
- [X] T021 [P] Note the new filler settings and log events in README.md
- [X] T022 Run `uv run pytest -q` and confirm no features 003/004 regressions, then walk the quickstart.md manual live-call checklist

---

## Dependencies

- **Setup (T001)** → blocks T005 (filler reads settings) and T013.
- **Foundational**: T002 → T003 → T004 (same file, sequential). T005 depends on T001. T005/T006 are [P] with T002/T003 (different files). All of Phase 2 blocks Phase 3+.
- **US1 (T007–T009)**: needs T006 (`log_welcome`). Independent of US2/US3 otherwise.
- **US2 (T010–T012)**: needs T003/T004 (event stream) + T006 (playback/barge-in logs).
- **US3 (T013–T016)**: builds on **US2's** `_handle_turn` refactor (T010) + T005 (filler) + T001 + T006. Do US2 before US3.
- **US4 (T017–T019)**: final wiring; assumes US1/US2/US3 log calls are in place (T007/T011/T014).
- **Polish**: after all stories.

## Parallel Execution Examples

- **Foundational**: T002, T005, T006 can run together (types.py, fillers.py, observability.py — distinct files); T003→T004 stay sequential (both service.py).
- **Per story tests**: T009, T012, T015+T016, T018+T019 are each [P] within their story (separate test files).
- Do **not** parallelize T007/T008/T010/T011/T013/T014/T017 — they all edit src/app/services/media/session.py.

## Implementation Strategy

- **MVP = US3 (tool filler) on top of US1+US2**: the "let me check" filler is the headline fix the
  user asked for, and it requires the US2 turn refactor and US1 greeting to be solid first. Deliver
  Setup → Foundational → US1 → US2 → US3 for a working, demoable MVP.
- **US4 (logging)** lands next as the observability increment — helpers already exist from
  Foundational (T006), so US4 is mostly wiring + tests.
- Each story is a shippable increment; run its checkpoint test before moving on.
