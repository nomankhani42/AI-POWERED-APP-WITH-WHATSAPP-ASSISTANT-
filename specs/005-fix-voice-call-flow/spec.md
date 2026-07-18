# Feature Specification: Fix Voice Call Flow (Welcome, Turn-Taking, Tool Fillers & Logging)

**Feature Branch**: `005-fix-voice-call-flow`

**Created**: 2026-07-08

**Status**: Draft

**Input**: User description: "the calling feature is little bit working great but few things to fix that when i call is attended accepted make sure it welcome message played then it turn of to speak customer and make sure it should printed all the logs in backend logs. when customer speaks then agent tts should be played when tool calls should call tool etc. research for it and fix the call flow when tool is calling message something like let me check for this or whatever"

## Clarifications

### Session 2026-07-08

- Q: How is the "let me check" filler produced when a tool runs? → A: The system detects the tool-call event in the agent's stream and speaks a filler automatically the instant a tool is about to run (deterministic, independent of the model's wording).
- Q: If the caller speaks during the welcome greeting, what happens? → A: The welcome is fully protected — it is not interruptible; caller speech during the welcome is discarded, and listening only begins once the greeting finishes playing.
- Q: How should filler phrasing/pacing work when a turn triggers tools? → A: The filler phrase is tailored to the specific tool/action being called (e.g. booking → "One moment, I'm booking that…"; availability/find → "Let me find that for you…"; cancel → "Let me cancel that…"; list → "Let me pull up your bookings…"), one filler per tool call.
- Q: Maximum silence after the caller finishes before they hear a filler or answer? → A: Within ~2 seconds.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reliable greeting then hand-off to the caller (Priority: P1)

When a caller places a call and the agent accepts it, the caller reliably hears a spoken welcome message first, and then the agent immediately goes quiet and starts listening so the caller can speak. The greeting always plays to completion before the agent begins listening for the caller's first words.

**Why this priority**: This is the very first moment of every call. If the greeting is skipped, cut off, or the agent never hands the turn back to the caller, the entire call fails at second zero. It is the foundation every other behavior depends on.

**Independent Test**: Place a test call, let it be accepted, and confirm the caller hears the full welcome message and can then speak and be heard without talking over the greeting or hitting dead air.

**Acceptance Scenarios**:

1. **Given** a caller places a call, **When** the agent accepts/attends it, **Then** the caller hears the full welcome message before any listening begins.
2. **Given** the welcome message has finished playing, **When** the caller starts speaking, **Then** the agent is already listening and captures the caller's speech.
3. **Given** the welcome message is still playing, **When** the caller stays silent, **Then** the agent does not cut off its own greeting and only starts listening once the greeting completes.
4. **Given** the welcome message is still playing, **When** the caller starts talking over it, **Then** the greeting is not interrupted, that speech is discarded, and listening only begins after the greeting finishes.

---

### User Story 2 - Caller speaks and hears a spoken agent reply (Priority: P1)

After the greeting, whenever the caller finishes speaking, the agent understands what was said, forms a reply, and speaks that reply back to the caller in a natural voice. Every completed caller utterance results in an audible agent response.

**Why this priority**: This is the core value of the call — a two-way spoken conversation. Without an audible reply to every caller turn, the caller is left in silence and the call is useless.

**Independent Test**: On an accepted call, speak a simple question after the greeting and confirm the agent replies out loud with a relevant answer, then returns to listening for the next turn.

**Acceptance Scenarios**:

1. **Given** the caller has finished an utterance, **When** the agent forms a reply, **Then** the caller hears the reply spoken aloud.
2. **Given** the agent has finished speaking a reply, **When** the caller speaks again, **Then** the agent handles the new utterance as the next turn.
3. **Given** the agent cannot produce a usable reply for a turn, **When** the turn completes, **Then** the caller hears a brief spoken fallback instead of unexplained silence.

---

### User Story 3 - Spoken "let me check" filler while a tool runs (Priority: P1)

When answering a caller requires the agent to look something up or perform an action (for example checking room availability, booking, cancelling, or listing bookings), the agent speaks a short natural filler phrase such as "Let me check that for you" before or as it runs the lookup, so the caller is not left in silence while the work happens. Once the lookup completes, the agent speaks the actual answer.

**Why this priority**: Lookups and actions can take a noticeable moment. Silence during that gap makes the caller think the call dropped and often causes them to repeat themselves or hang up. A spoken filler keeps the call feeling alive and is exactly the behavior the user asked for.

**Independent Test**: On an accepted call, ask something that requires a lookup (e.g. "What rooms are available next weekend?") and confirm the agent audibly says a short filler like "Let me check that for you" before speaking the retrieved answer.

**Acceptance Scenarios**:

1. **Given** the caller asks something that triggers a lookup or action, **When** the agent begins the lookup, **Then** the caller hears a short spoken filler phrase before the answer.
2. **Given** a filler phrase has been spoken, **When** the lookup completes, **Then** the caller hears the actual result spoken as a normal reply.
3. **Given** a caller turn is answered without any lookup, **When** the agent replies, **Then** no filler phrase is inserted (fillers only accompany lookups/actions).
4. **Given** the lookup fails, **When** the agent finishes the turn, **Then** the caller hears a graceful spoken explanation rather than silence after the filler.

---

### User Story 4 - Full call flow visible in backend logs (Priority: P2)

Every meaningful step of a live call is recorded in the backend logs so an operator can follow exactly what happened on a call end-to-end: the call being accepted, the welcome playing, each caller utterance transcribed, each agent reply, each tool/lookup invoked (with which action and its outcome), each filler phrase spoken, playback start/stop, barge-ins, silence re-prompts, fallbacks, and the call ending. Logs are correlated by call so one call's events can be read as a single timeline.

**Why this priority**: The user explicitly wants the whole flow "printed in backend logs." Without this, diagnosing why a greeting didn't play, a reply wasn't spoken, or a tool didn't fire is guesswork. It is essential for operating and debugging the call feature but does not itself change the caller experience, so it sits just below the P1 conversational behaviors.

**Independent Test**: Place a test call that includes a lookup, then read the backend logs and confirm the full sequence — accepted → welcome → caller transcript → tool invoked + outcome → filler → reply → call ended — is present and tied to the same call identifier.

**Acceptance Scenarios**:

1. **Given** a call is accepted, **When** the call proceeds, **Then** each stage (accept, welcome, each transcript, each reply, each tool call and its outcome, playback events, and call end) is written to the backend logs.
2. **Given** multiple calls happen, **When** an operator reads the logs, **Then** each log line identifies which call it belongs to so per-call timelines can be reconstructed.
3. **Given** a tool/lookup is invoked during a turn, **When** it runs, **Then** the log records which action ran and whether it succeeded or failed.
4. **Given** any stage fails (transcription, reply, or speech), **When** the failure occurs, **Then** the failure is logged with enough context to identify the call and the stage — without logging secrets or credentials.

---

### Edge Cases

- **Caller talks over the greeting**: The greeting is fully protected and non-interruptible; the agent plays it to completion and speech during the greeting is discarded (barge-in does not apply to the welcome). Listening only begins after the greeting finishes.
- **Caller is silent after the greeting**: The agent re-prompts once ("Are you still there?") and, on continued silence, ends the call gracefully — each of these is logged.
- **Tool runs long**: The filler covers the wait; if the lookup exceeds a reasonable time, the caller still eventually hears either the result or a graceful failure message, never indefinite silence.
- **Multiple tools in one turn**: Each tool call gets its own tailored filler describing that specific action, so chained lookups (e.g. check availability → book) sound like natural narration ("Let me find that…" then "One moment, I'm booking that…") rather than a repeated generic phrase.
- **Reply is empty**: The caller hears a brief spoken fallback rather than silence, and the empty reply is logged.
- **Concurrent calls**: Greeting, turn-taking, fillers, and logs remain correctly separated per call with no cross-talk.
- **Speech synthesis fails**: The turn's failure is logged and the caller hears a short apology instead of dead air.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When a call is accepted/attended, the system MUST play the configured welcome message to the caller as the opening turn before listening begins.
- **FR-002**: The system MUST finish playing the welcome message and only then begin listening for the caller, so the greeting is not cut off by its own listening loop.
- **FR-003**: After the greeting completes, the system MUST hand the turn to the caller and capture the caller's speech.
- **FR-004**: For every completed caller utterance, the system MUST produce an agent reply and speak that reply audibly back to the caller.
- **FR-005**: After speaking a reply, the system MUST return to listening for the caller's next utterance, continuing the turn-by-turn loop until the call ends.
- **FR-006**: When a caller turn requires a lookup or action, the system MUST detect the tool-call in the agent's stream and automatically speak a short filler phrase to the caller the instant the tool is about to run — one filler per tool call — so the filler fires deterministically regardless of the model's wording.
- **FR-007**: The system MUST speak the actual result of the lookup/action to the caller after the filler, as the reply for that turn.
- **FR-008**: The system MUST NOT insert a filler phrase for turns that are answered without any lookup or action.
- **FR-009**: The filler phrase MUST be tailored to the specific tool/action being called so it describes what the agent is doing (e.g. booking → "One moment, I'm booking that…"; availability/find → "Let me find that for you…"; cancellation → "Let me cancel that…"; listing → "Let me pull up your bookings…"). Each known tool MUST have its own matching phrase, with a generic fallback for any tool that lacks a specific one.
- **FR-010**: If a lookup/action fails, the system MUST still speak a graceful explanation to the caller for that turn instead of leaving silence after the filler.
- **FR-011**: The system MUST write a backend log record when a call is accepted/attended, identifying the call and caller.
- **FR-012**: The system MUST write a backend log record for the welcome message being played.
- **FR-013**: The system MUST write a backend log record for each caller utterance transcribed and for each agent reply spoken.
- **FR-014**: The system MUST write a backend log record for each tool/lookup invoked during a turn, including which action ran and whether it succeeded or failed.
- **FR-015**: The system MUST write a backend log record for each spoken filler phrase, playback start/stop, barge-in, silence re-prompt, spoken fallback/apology, and the call ending.
- **FR-016**: Every call-related log record MUST carry a call identifier so an operator can reconstruct a single call's timeline from the logs.
- **FR-017**: Log records for the key flow milestones MUST be emitted at a visibility level that appears in normal backend logs (not suppressed as debug-only), while never logging secrets, tokens, or credentials.
- **FR-018**: The system MUST keep greeting, turn-taking, fillers, and logs correctly isolated per call when multiple calls are active at once.
- **FR-020**: Barge-in (caller interrupting playback) MUST apply only to agent replies, never to the welcome greeting; the greeting always plays to completion and caller audio during it is discarded.
- **FR-021**: After the caller finishes speaking, the caller MUST hear either a filler phrase or the reply within approximately 2 seconds, so a lookup or reasoning delay never leaves more than ~2 seconds of silence.
- **FR-019**: On any stage failure (transcription, reply generation, or speech synthesis) the system MUST log the failure with call and stage context and keep the caller experience graceful (apology or graceful end), never hanging in silence.

### Key Entities *(include if data involved)*

- **Call**: A single live voice call, identified by a call id and the caller's number; has a lifecycle (accepted → in conversation → ended) and an ordered sequence of turns.
- **Conversation Turn**: One exchange within a call — an optional caller transcript and the agent's spoken reply — numbered in order (welcome = turn 0), with start/end timing.
- **Tool/Lookup Invocation**: An action taken during a turn to answer the caller (availability check, booking, cancellation, listing), with the action name and its success/failure outcome, and the filler phrase that accompanied it.
- **Call Log Record**: A backend log entry tied to a call id capturing one flow milestone (accepted, welcome, transcript, reply, tool invocation + outcome, filler, playback event, re-prompt, fallback, end, or failure).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of accepted test calls, the caller hears the complete welcome message before the agent starts listening.
- **SC-002**: In at least 95% of caller turns, a completed caller utterance results in an audible agent reply (the remainder degrade to a spoken fallback, never silence).
- **SC-003**: In 100% of caller turns that require a lookup or action, the caller hears a filler phrase before the answer, and the silence between the caller finishing and hearing that filler (or the answer) never exceeds ~2 seconds.
- **SC-004**: In 100% of turns answered without a lookup, no filler phrase is spoken.
- **SC-005**: For any completed test call, an operator can reconstruct the entire call timeline — accept, welcome, every transcript, every reply, every tool invocation and outcome, and call end — from the backend logs alone, all tied to one call identifier.
- **SC-006**: No backend log record for a call exposes any secret, token, or credential.
- **SC-007**: With multiple concurrent test calls, 100% of log records and spoken turns are attributed to the correct call with no cross-talk.

## Assumptions

- The existing voice pipeline (call acceptance, speech-to-text, agent reasoning with tools, and text-to-speech) is already in place and working "a little bit"; this feature fixes and completes the call flow rather than building the pipeline from scratch.
- The welcome message text is configurable and already exists as a setting; this feature ensures it plays reliably and hands off correctly.
- The tools that trigger fillers are the existing booking-related actions (availability, booking, cancellation, listing) plus any future tools; a filler applies whenever any tool runs during a turn.
- Filler phrases are tailored per tool/action (see FR-009); the exact wording of each phrase is a copy detail, not a scope decision, but the mapping of one phrase per known tool is in scope.
- "Print all the logs in backend logs" means the key call-flow milestones are logged at a normally-visible log level and correlated by call id, not that every low-level internal detail is dumped.
- Backend logs are the operator's primary way to observe and debug calls in this environment; no separate dashboard is required for this feature.
- Barge-in (caller interrupting the agent) and silence handling already exist and should continue to work for agent replies; this feature keeps them intact, scopes barge-in to replies only (not the welcome, per FR-020), and ensures they are logged.
