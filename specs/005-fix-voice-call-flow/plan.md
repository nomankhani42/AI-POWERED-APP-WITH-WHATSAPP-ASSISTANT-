# Implementation Plan: Fix Voice Call Flow (Welcome, Turn-Taking, Tool Fillers & Logging)

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Branch**: `005-fix-voice-call-flow` | **Date**: 2026-07-08 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-fix-voice-call-flow/spec.md`

## Summary

The per-call conversation loop (feature 004, `src/app/services/media/session.py`) already plays a
welcome, then runs a listen → transcribe → stream-reply → TTS loop with barge-in, silence handling,
and structured logging. This feature closes three gaps the spec calls out:

1. **Tool fillers (US3, P1)** — the agent turn currently streams *only* text deltas
   (`run_turn_stream` in `agent/service.py` discards everything that isn't a
   `ResponseTextDeltaEvent`), so the caller hears dead silence while a booking/availability tool
   runs. We switch the turn to emit **structured stream events** so the media session can detect a
   tool call the instant it starts and speak a **tool-tailored filler** (booking → "One moment, I'm
   booking that…"; availability → "Let me find that for you…"; etc.), then speak the real answer.
2. **Full backend logging (US4, P2)** — `observability.py` only logs `call_attended` and
   `call_turn`. We extend it to record every flow milestone (welcome, transcript, reply, tool call
   + outcome, filler, playback start/stop, barge-in, re-prompt, fallback, end) correlated by
   `call_id`.
3. **Greeting reliability & turn-taking (US1/US2, P1)** — the welcome is made explicitly
   non-interruptible (barge-in scoped to replies only, FR-020) and the welcome→listen→reply chain
   is verified end-to-end, keeping the ~2 s filler/answer latency budget (FR-021).

**Technical approach**: The tool-call hook uses the OpenAI Agents SDK streaming API. Per Context7
(`/openai/openai-agents-python`, Phase 0), `Runner.run_streamed().stream_events()` emits a
`run_item_stream_event` whose `item.type == "tool_call_item"` when a tool is about to run and
`"tool_call_output_item"` when it returns; the tool name is on the item's `raw_item.name`. We keep
text streaming into Cartesia unchanged and layer the filler on top. No new provider, no new
persistence — only in-process orchestration and logging change.

## Technical Context

**Language/Version**: Python 3.12 (`requires-python = ">=3.12"`)

**Primary Dependencies**: FastAPI + Uvicorn, `openai-agents>=0.17.7` (Agents SDK, GPT-4.1),
`cartesia>=3.3.0` (Sonic TTS), `deepgram-sdk>=7.4.0` (STT), `aiortc>=1.14.0` (WebRTC media),
`redis>=8.0.1` (session), `beanie>=2.1.0` (MongoDB), `pydantic-settings>=2.4`. Managed with `uv`.

**Storage**: MongoDB (durable `Call`/`CallEvent`) + Redis (short-term session/turn state). **This
feature adds no new persistence** — call-flow observability is emitted to the standard logging
pipeline (FR-011–017); the tailored fillers are transient speech.

**Testing**: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) + `fakeredis`. Tool-call and
filler behavior is tested by driving the loop with a fake agent event stream and a fake media
bridge (no live providers), matching the existing 003/004 test style.

**Target Platform**: Linux server (async FastAPI service behind a public webhook).

**Project Type**: Single async web service (voice backend under `src/app/`).

**Performance Goals**: Welcome always plays in full before listening (SC-001); ≥95 % of caller turns
get an audible reply else a spoken fallback (SC-002); caller hears a filler or answer within ~2 s of
finishing speaking (SC-003/FR-021); no filler on no-tool turns (SC-004).

**Constraints**: Never block the webhook 200 ack (existing `_fire_and_forget`); strict per-call
isolation for greeting, turn-taking, fillers, and logs (FR-018/SC-007); no secrets in logs
(FR-017/SC-006); barge-in applies to replies only, never the welcome (FR-020).

**Scale/Scope**: Concurrent live calls, one `_CallSession` per `call_id`. Scope is the agent turn
(`agent/service.py`), the media session loop (`services/media/session.py`), observability
(`services/media/observability.py`), a small filler-phrase helper, and config additions — plus
tests. No endpoint/contract changes to the WhatsApp webhook.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Assessment | Status |
|-----------|------------|--------|
| I. Modular Architecture | Change is confined to existing cohesive modules; the tool→filler phrase mapping is a small in-place helper (per user preference: extract helpers in-place, do not spin up a new package). No module mixes transport + logic + I/O anew. | ✅ PASS |
| II. Async-First FastAPI | All new work (event stream consumption, filler TTS) is async and non-blocking; no blocking calls added to the event path. | ✅ PASS |
| III. Layered Memory | No new state. Redis session usage in the agent turn is unchanged; no hot-path Mongo reads added. | ✅ PASS |
| IV. Voice Pipeline Integrity | Keeps the fixed Deepgram → OpenAI Agents (GPT-4.1) → Cartesia Sonic contract; the filler reuses the existing TTS stage behind `synthesize_stream`. Tool-call failures degrade to a spoken explanation (FR-010/019), never silent-dropped. | ✅ PASS |
| V. Configuration & Secrets | Filler phrases and welcome are typed settings (defaults provided); logs never emit tokens/secrets (FR-017). | ✅ PASS |
| VI. Documentation-Driven (Context7 & sub-agents) | Agents SDK streaming event shape verified via Context7 in Phase 0 before coding; Cartesia interface already verified in `tts.py`. | ✅ PASS |

**Result**: All gates pass. No entries required in Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/005-fix-voice-call-flow/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (internal interface contracts)
│   ├── agent-turn-events.md
│   ├── filler-phrases.md
│   └── call-observability-log.md
├── checklists/
│   └── requirements.md  # (from /speckit-specify + /speckit-clarify)
└── tasks.md             # /speckit-tasks output (NOT created here)
```

### Source Code (repository root)

```text
src/app/
├── agent/
│   ├── service.py        # CHANGE: run_turn_events(...) yields structured AgentStreamEvent
│   │                     #         (text_delta | tool_call | tool_output); run_turn_stream kept
│   │                     #         or wrapped for the text-only WhatsApp chat path.
│   ├── assistant.py      # (unchanged) GPT-4.1 agent + TOOLS
│   └── tools.py          # (unchanged) booking tools — names drive filler selection
├── services/
│   └── media/
│       ├── session.py    # CHANGE: _handle_turn consumes AgentStreamEvent; on tool_call speak
│       │                 #         tailored filler + log; stream reply text to TTS as today.
│       ├── observability.py  # CHANGE: add log_welcome, log_transcript?, log_tool_call,
│       │                 #         log_tool_result, log_filler, log_playback, log_barge_in,
│       │                 #         log_reprompt, log_fallback, log_call_ended (all call_id-tagged).
│       ├── fillers.py    # NEW (small helper): tool_name -> filler phrase mapping + fallback.
│       └── types.py      # CHANGE: add AgentStreamEvent (+ kind enum); ConversationTurn unchanged.
├── core/
│   └── config.py         # CHANGE: add filler-phrase settings (per-tool defaults + generic
│                         #         fallback); welcome_message already present.
└── (tts.py / stt.py / webrtc.py / meta_calling.py unchanged)

tests/
├── unit/
│   ├── test_fillers.py           # NEW: tool_name -> phrase mapping, fallback for unknown tool.
│   └── test_observability.py     # NEW/EXTEND: each log helper emits call_id + no secrets.
└── integration/
    ├── test_session_tool_filler.py   # NEW: fake agent stream w/ tool_call -> filler spoken
    │                                  #      before reply; no filler on no-tool turn.
    ├── test_session_welcome.py       # NEW/EXTEND: welcome plays fully, non-interruptible (FR-020).
    └── test_session_logging.py       # NEW: full-timeline log assertions, per-call isolation.
```

**Structure Decision**: Single async web service, existing `src/app/` layout. The feature edits
four existing modules and adds one small helper (`fillers.py`) plus tests — no new package or
service. This honors Principle I (modular, single-responsibility files) and the recorded preference
to extract helpers in-place rather than restructure into packages.

## Complexity Tracking

> No Constitution violations — table intentionally empty.
