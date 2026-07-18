# Phase 0 Research: Fix Voice Call Flow

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

All open questions from the spec's clarifications were resolved during `/speckit-clarify`
(filler mechanism = tool-event detection; welcome = non-interruptible; filler = per-tool tailored;
latency ≤ ~2 s). The only remaining technical unknown was **how the OpenAI Agents SDK exposes tool
calls during streaming**, which drives the entire filler design. Verified via Context7 per
constitution Principle VI.

## `1 — Detecting a tool call mid-stream (OpenAI Agents SDK)

**Source**: Context7 `/openai/openai-agents-python` — `docs/streaming.md`, `docs/context.md`,
`docs/tools.md` (installed `openai-agents>=0.17.7`).

- **Decision**: Consume `Runner.run_streamed(...).stream_events()` and branch on
  `event.type == "run_item_stream_event"`:
  - `event.item.type == "tool_call_item"` → a tool is **about to run**. This is the trigger to
    speak the tailored filler (fires *before* the tool executes, so the filler covers the wait).
  - `event.item.type == "tool_call_output_item"` → the tool **returned**; `event.item.output`
    carries the result. Use this to log the tool outcome (success/failure).
  - `event.type == "raw_response_event"` + `isinstance(event.data, ResponseTextDeltaEvent)` → the
    reply **text delta** (the existing path). These arrive *after* the tool output, so the natural
    call order per turn is: `tool_call_item` (speak filler) → tool runs → `tool_call_output_item`
    (log) → text deltas (stream to TTS as the answer).
- **Reading the tool name**: for a function tool the item's `raw_item` is the Responses API
  `ResponseFunctionToolCall`, whose `.name` is the tool name (matches `@function_tool` inferred
  names: `check_availability`, `book_room`, `cancel_booking`, `list_bookings`). Access defensively
  (`getattr(item.raw_item, "name", None)`) and fall back to the generic filler if absent.
- **Rationale**: This is the documented, model-independent hook. It fires deterministically whenever
  the model decides to call a tool, satisfying FR-006 (filler always fires on a tool) without
  relying on the model to phrase a lead-in itself (Q1 = Option A). The existing text-delta path is
  untouched, so streaming reply → TTS keeps working.
- **Alternatives considered**:
  - *Prompt the model to say a lead-in itself* (Q1 Option B) — rejected: non-deterministic (model
    may skip it), and harder to keep the filler tool-specific and to log reliably.
  - *Poll `RunItemStreamEvent.name == "tool_called"`* — equivalent signal (the SDK also exposes a
    semantic `name`), but branching on `item.type` is what the official example uses and gives
    direct access to the item for the tool name; we use `item.type` as primary, `name` as a
    cross-check only if needed.

## `2 — Turn orchestration with an interleaved filler

- **Decision**: Replace the text-only `run_turn_stream` consumption in `_handle_turn` with a
  structured event stream (`run_turn_events`). The session iterates events: on `tool_call` it
  `await`s the tailored filler through the **existing** `_speak`/`synthesize_stream` path (blocking
  for the short filler), logs the tool call; on `tool_output` it logs the outcome; text deltas are
  buffered into an async generator that feeds `synthesize_stream` for the reply exactly as today,
  preserving barge-in on the reply.
- **Rationale**: Keeps a single agent run per turn (no extra model calls), reuses the proven TTS
  stage for the filler, and keeps barge-in scoped to the reply. The filler is spoken while the tool
  executes, so total added latency is just the short filler's own playback, well within the ~2 s
  budget (FR-021/SC-003).
- **Alternatives considered**: running the agent to completion first then deciding on a filler —
  rejected: defeats the purpose (the caller would already have sat in silence during the tool).

## `3 — Filler phrase mapping (tool-tailored)

- **Decision**: A small `fillers.py` helper maps tool name → phrase, with a generic fallback:
  `check_availability` → "Let me find that for you…"; `book_room` → "One moment, I'm booking
  that…"; `cancel_booking` → "Let me cancel that…"; `list_bookings` → "Let me pull up your
  bookings…"; unknown/None → generic "Let me check that for you…". Phrases are overridable via
  settings so wording can change without touching the loop.
- **Rationale**: Satisfies Q3/FR-009 (per-action wording) while staying a pure, unit-testable
  function. Kept as an in-place helper module, not a new package (recorded user preference).
- **Alternatives considered**: single fixed phrase (rejected by Q3); random rotation (rejected —
  user wants the phrase to describe the specific action).

## `4 — Welcome greeting: non-interruptible (barge-in scope)

- **Source**: existing `session.py` — the welcome uses `_speak` (no barge-in), while replies use
  `_play_with_barge_in`.
- **Decision**: Keep the welcome on the non-barge-in `_speak` path and make the scope explicit via
  `FR-020`: barge-in applies to replies only. Caller audio arriving during the welcome is not
  consumed (the STT listen loop only starts after `_play_welcome` returns), so it is discarded (Q2 =
  Option B). No functional change to `_speak`; this is a verification + documentation + logging item.
- **Rationale**: Matches the clarified decision and the current code path; the main work is
  confirming the welcome always completes and adding a `welcome` log record (FR-012).

## `5 — Observability vocabulary & secret-safety

- **Source**: existing `observability.py` (uses `logger.info` + a structured `extra` dict).
- **Decision**: Extend the same pattern with one helper per milestone, each taking `call_id` and
  emitting at `INFO` (visible in normal backend logs, FR-017). Log the tool **name** and a boolean
  success/failure for tool outcomes — never tool argument values that could echo PII, and never
  tokens/secrets (FR-017/SC-006). Filler, playback start/stop, barge-in, re-prompt, fallback, and
  call-ended each get a record; all carry `call_id` for per-call timeline reconstruction (FR-016).
- **Rationale**: Reuses the proven structured-logging approach; INFO level ensures the flow actually
  "prints in backend logs" as the user asked, without dumping low-level frames.
- **Alternatives considered**: DEBUG-level logging (rejected — user wants it visible by default);
  persisting each event to Mongo (rejected — spec scopes this to logs; adds no value here).

## `6 — Packages requiring docs (per user request: "use context7")

| Package | Needed for | Docs consulted |
|---------|-----------|----------------|
| `openai-agents` (Agents SDK) | Tool-call detection in `stream_events()`, tool name access | ✅ Context7 `/openai/openai-agents-python` (§1) |
| `cartesia` | TTS streaming for filler + reply | Already verified in `src/app/services/tts.py` header against `cartesia==3.3.0`; no new usage — reused as-is |
| `deepgram-sdk` | STT listen loop | Unchanged by this feature; no new API surface |
| `aiortc` | Media bridge playback / `stop_playback` | Unchanged; reused as-is |

Only the Agents SDK required fresh doc lookup; everything else reuses interfaces already verified in
prior features.

## Resolved unknowns

All NEEDS CLARIFICATION items are resolved. No blocking unknowns remain for Phase 1.
