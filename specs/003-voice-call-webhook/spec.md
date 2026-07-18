# Feature Specification: Meta Voice Call Webhook & Speech Services

**Feature Branch**: `003-voice-call-webhook`

**Created**: 2026-07-05

**Status**: Draft

**Input**: User description: "create a route for meta api web hook handling call also create services for text to speech and speech to text" — refined with: "when a call is attended, log that the call was attended along with the caller's number; a welcome message plays automatically on connect; when the user speaks, capture real-time audio chunks and stream them to the backend; the agent's response is converted to speech (TTS) and played back to the caller; then the next round of the listen→respond loop begins."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Receive and respond to Meta call events (Priority: P1)

A customer initiates or receives a voice call through the business's Meta (WhatsApp) channel. Meta notifies the platform of the call lifecycle — verification handshake, incoming call, connection established, and call ended — by sending events to a single webhook address. The platform must acknowledge Meta's initial verification, accept authentic call events, and reject anything that cannot be trusted.

**Why this priority**: Without a reachable, verified webhook that Meta can deliver call events to, no voice interaction can begin. It is the entry point on which everything else depends and delivers immediate value: the business becomes reachable by voice and every call is reliably tracked from start to finish.

**Independent Test**: Configure the webhook address with Meta, complete the verification challenge, then simulate the sequence of call events. Confirm each event is acknowledged within the provider's required time window and recorded, and that a call's full lifecycle (start → connected → ended) is captured.

**Acceptance Scenarios**:

1. **Given** Meta sends a verification request with the expected token, **When** the request reaches the webhook, **Then** the platform echoes back the challenge and the endpoint becomes verified.
2. **Given** an incoming-call event arrives from Meta, **When** it passes authenticity checks, **Then** the platform acknowledges receipt within the required time window and creates a record of the call.
3. **Given** a call-ended event arrives for a known call, **When** it is processed, **Then** the call record is updated to a completed state with its end time.
4. **Given** an event arrives whose authenticity cannot be verified, **When** it reaches the webhook, **Then** the platform rejects it and does not create or alter any call record.
5. **Given** a duplicate event for a call already processed, **When** it arrives, **Then** the platform acknowledges it without creating a duplicate record.

---

### User Story 2 - Transcribe caller speech to text (Priority: P2)

While a call is active, the words a caller speaks must be turned into text so the assistant can understand the request. A reusable speech-to-text capability accepts spoken audio from the call and returns an accurate text transcript.

**Why this priority**: Understanding the caller is the first half of a meaningful voice conversation. It builds directly on the webhook (P1) and is a prerequisite for the assistant producing any relevant answer. It is valuable on its own — even before spoken replies exist, transcripts let the business see what callers ask.

**Independent Test**: Provide sample spoken audio to the speech-to-text capability and confirm it returns a text transcript that matches the spoken content, including handling of silence and background noise, within an acceptable latency.

**Acceptance Scenarios**:

1. **Given** a clear audio segment of a caller speaking, **When** it is submitted for transcription, **Then** the returned text accurately reflects the spoken words.
2. **Given** an audio segment containing only silence, **When** it is submitted, **Then** the capability returns an empty or clearly-flagged result rather than an error.
3. **Given** the transcription provider is temporarily unavailable, **When** transcription is attempted, **Then** the failure is handled explicitly and surfaced so the call can degrade gracefully rather than hang.

---

### User Story 3 - Speak the assistant's reply to the caller (Priority: P3)

When the assistant has formulated a text response, that text must be turned into natural-sounding speech and played back to the caller. A reusable text-to-speech capability accepts response text and returns spoken audio suitable for the live call.

**Why this priority**: Spoken replies complete the conversational loop and deliver the full hands-free experience. It depends on there being something to say (the assistant's output), so it follows understanding the caller. It is independently demonstrable by converting any text into audio.

**Independent Test**: Provide sample response text to the text-to-speech capability and confirm it returns audio that, when played, clearly and naturally speaks the provided text within an acceptable latency.

**Acceptance Scenarios**:

1. **Given** a text response from the assistant, **When** it is submitted for synthesis, **Then** the capability returns audio that speaks the text clearly and naturally.
2. **Given** an empty text input, **When** synthesis is attempted, **Then** the capability returns no audio and reports the empty input rather than failing unexpectedly.
3. **Given** the synthesis provider is temporarily unavailable, **When** synthesis is attempted, **Then** the failure is handled explicitly so the call can fall back gracefully instead of stalling.

---

### User Story 4 - Hold an automatic back-and-forth voice conversation (Priority: P2)

When a caller's voice call is attended, the platform records that the call was attended (including the caller's number) and immediately greets the caller with an automatic welcome message — the caller does not need to speak first. From that point the platform runs a continuous conversational loop: as the caller speaks, their audio is captured in real-time chunks and streamed to the backend for transcription; the transcript is handed to the assistant; the assistant's reply is spoken back to the caller; and the platform returns to listening for the caller's next turn. This repeats turn-by-turn until the call ends.

**Why this priority**: This is the integrating experience that turns the individual capabilities (webhook, speech-to-text, text-to-speech) into an actual voice assistant. It orchestrates User Stories 1–3 into a live, hands-free conversation and is what a caller actually experiences. It is prioritized just below the foundational webhook because it depends on that entry point and on both speech capabilities being present.

**Independent Test**: Place (or simulate) an attended call, confirm a log entry appears recording the call as attended with the caller's number, confirm the welcome message plays automatically on connect, then speak a phrase and confirm real-time chunks are transcribed, the assistant replies in speech, and the platform loops back to listening for the next turn — repeating for multiple turns until hang-up.

**Acceptance Scenarios**:

1. **Given** a call is attended/connected, **When** the connection is established, **Then** the platform writes a log entry recording that the call was attended together with the caller's number and call identifier.
2. **Given** a call has just connected, **When** no caller speech has occurred yet, **Then** a welcome message is synthesized and played to the caller automatically without waiting for the caller to speak.
3. **Given** the welcome message has finished, **When** the caller begins speaking, **Then** their audio is captured in real-time chunks and streamed to the backend for incremental transcription rather than waiting for the entire utterance or call to complete.
4. **Given** the caller finishes a turn (detected via silence/endpointing), **When** the transcript is passed to the assistant, **Then** the assistant's text reply is converted to speech and played back to the caller.
5. **Given** the assistant's reply has finished playing, **When** the caller speaks again, **Then** the platform begins the next round of the listen → transcribe → respond loop, continuing turn-by-turn until the call ends.
6. **Given** an active conversation, **When** the caller hangs up or the call ends, **Then** the loop terminates cleanly, no further audio is processed, and the call record and turn history are finalized.

---

### Edge Cases

- **Verification token mismatch**: A verification request with a wrong or missing token is rejected without exposing why.
- **Out-of-order events**: A call-ended event arriving before its corresponding call-start event is reconciled without creating an orphaned or inconsistent record.
- **Slow acknowledgement**: If downstream processing is slow, the webhook still acknowledges Meta within the required window to avoid provider retries and back-pressure.
- **Provider retries / duplicates**: Repeated delivery of the same event is treated idempotently.
- **Unsupported or corrupt audio**: Audio that cannot be decoded is rejected with a clear error rather than silently dropped.
- **Very long utterances**: Speech longer than a normal turn is handled without truncating mid-word or exceeding acceptable latency.
- **Concurrent calls**: Multiple simultaneous calls are transcribed and synthesized independently without cross-talk between sessions.
- **Silent caller after welcome**: If the caller says nothing for a configurable period after the welcome message, the platform re-prompts or ends the call gracefully rather than looping silently forever.
- **Caller talks over the welcome/reply**: If the caller begins speaking while the welcome message or an assistant reply is still playing, the platform handles the overlap according to its defined turn-taking behavior rather than losing the caller's speech or deadlocking.
- **Dropped or out-of-order audio chunks**: Lost, delayed, or out-of-order real-time audio chunks are handled so the transcript is reconstructed as accurately as possible without stalling the loop.
- **Assistant produces no reply**: If the assistant returns empty or errored output for a turn, the caller hears a graceful fallback prompt rather than silence, and the loop continues.
- **Very long-running call**: A call with many turns continues to loop without unbounded growth in memory or latency degradation per turn.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The platform MUST expose a single webhook address that Meta can call for voice-call events.
- **FR-002**: The platform MUST complete Meta's verification handshake by validating the provided token and echoing the challenge when the token is correct.
- **FR-003**: The platform MUST verify the authenticity of every incoming event before acting on it, and MUST reject events that fail verification.
- **FR-004**: The platform MUST acknowledge accepted events within the time window required by Meta to prevent redelivery.
- **FR-005**: The platform MUST recognise and record the call lifecycle stages it receives (at minimum: call started, call connected, call ended), persisting each call and its final state.
- **FR-006**: The platform MUST process events idempotently so that duplicate deliveries do not create duplicate records or repeated side effects.
- **FR-007**: The platform MUST provide a reusable speech-to-text capability that accepts caller audio and returns a text transcript.
- **FR-008**: The platform MUST provide a reusable text-to-speech capability that accepts text and returns spoken audio.
- **FR-009**: Both speech capabilities MUST handle provider failures explicitly (timeout, error, or fallback) and surface the outcome rather than hang or fail silently.
- **FR-010**: Both speech capabilities MUST be usable independently of the webhook (e.g., invoked directly for testing) so each stage of the voice pipeline can be exercised in isolation.
- **FR-011**: The speech-to-text capability MUST handle empty or silent audio by returning an empty/flagged result instead of an error.
- **FR-012**: The text-to-speech capability MUST reject empty input with a clear, reported outcome instead of producing invalid audio.
- **FR-013**: The platform MUST keep separate concurrent calls isolated so audio, transcripts, and synthesized replies never bleed between sessions.
- **FR-014**: All credentials and endpoints required to reach Meta and the speech providers MUST be supplied through configuration and MUST never be exposed in logs or responses.
- **FR-015**: When a call is attended/connected, the platform MUST write a log entry recording that the call was attended, including at minimum the caller's number and the call identifier, so attended calls are observable in the logs.
- **FR-016**: On call connection, the platform MUST automatically play a welcome/greeting message to the caller without requiring the caller to speak first.
- **FR-017**: While the caller speaks, the platform MUST capture the caller's audio in real-time chunks and stream them to the backend for incremental transcription, rather than waiting for the entire utterance or the whole call to complete.
- **FR-018**: The platform MUST detect the end of the caller's speaking turn (e.g., via silence/endpointing) and pass the resulting transcript to the assistant for a response.
- **FR-019**: The platform MUST convert the assistant's text reply to speech and play it back to the caller for each turn.
- **FR-020**: After an assistant reply finishes, the platform MUST return to listening for the caller's next turn, running the listen → transcribe → respond loop repeatedly until the call ends or the caller hangs up.
- **FR-021**: The conversation loop MUST terminate cleanly when the call ends, stopping audio capture and processing and finalizing the call record and its turn history.
- **FR-022**: The welcome message content MUST be configurable (not hardcoded per call site) so it can be changed without altering the conversation logic.
- **FR-023**: The platform MUST associate each conversational turn (caller transcript and assistant reply) with its call for observability and later review.

### Key Entities *(include if data involved)*

- **Call**: A single voice interaction with a caller. Key attributes: a unique call identifier from Meta, the caller's identity, current lifecycle state (e.g., started, connected, ended), start and end times, and a link to its conversation/session.
- **Call Event**: A single notification from Meta about a call. Key attributes: event type, the call it refers to, a delivery identifier used for idempotency, and receipt time.
- **Transcript Segment**: Text produced from a portion of caller audio. Key attributes: the owning call/session, the recognized text, and ordering/timing within the call.
- **Speech Response**: Audio produced from assistant text. Key attributes: the owning call/session, the source text, and the resulting audio reference.
- **Conversation Turn**: One exchange within a call's loop. Key attributes: the owning call/session, turn order/sequence number, the caller's transcript for the turn, the assistant's reply text, and timing (turn start/end). The automatic welcome counts as the opening turn.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid Meta verification requests result in a successfully verified endpoint on first attempt.
- **SC-002**: At least 99% of authentic call events are acknowledged within Meta's required response window, measured over a rolling day.
- **SC-003**: 0 duplicate call records result from repeated/duplicate event deliveries in idempotency testing.
- **SC-004**: 100% of events that fail authenticity checks are rejected and leave no record.
- **SC-005**: Caller speech is transcribed and the assistant's spoken reply begins playing fast enough to sustain a natural conversation, with end-to-end turn latency perceived as responsive by callers in usability testing.
- **SC-006**: Transcription accurately reflects spoken content in at least 90% of clear-audio test utterances.
- **SC-007**: Each of the three capabilities (webhook handling, speech-to-text, text-to-speech) can be independently demonstrated and tested in isolation.
- **SC-008**: 100% of provider-failure scenarios in testing result in an explicit, graceful outcome (no hangs, no silent drops).
- **SC-009**: 100% of attended calls produce a log entry recording the call as attended with the caller's number, verifiable in the logs.
- **SC-010**: The welcome message begins playing automatically on connect without any caller speech, within a delay short enough to feel immediate to the caller in usability testing.
- **SC-011**: A caller can complete a multi-turn conversation (at least 5 back-and-forth turns) in a single call with the listen → respond loop continuing correctly on every turn.
- **SC-012**: Caller speech is captured and streamed as real-time chunks such that transcription begins before the caller finishes speaking, keeping per-turn response latency perceived as responsive.

## Assumptions

- The voice channel is Meta's WhatsApp Business calling capability, consistent with the platform's existing Meta/WhatsApp integration; the same webhook infrastructure and verification model apply.
- Verification uses Meta's standard token-challenge handshake and event-authenticity signatures; no custom scheme is required.
- The speech-to-text and text-to-speech capabilities are delivered as internal, reusable services with stable interfaces so their underlying providers can be swapped without changing callers.
- Call and conversation records reuse the platform's existing persistence and session model rather than introducing a separate store.
- Acceptable latency, audio formats, and language/locale defaults follow standard real-time voice expectations for the platform's target market unless later specified.
- Actual audio streaming/media transport mechanics with Meta (how raw audio is exchanged during a live call) are assumed to follow Meta's documented calling media flow and are treated as an integration detail of the webhook/call layer.
- Turn-taking is assumed to be sequential (half-duplex): the platform listens after finishing each spoken message. Full barge-in (interrupting the assistant mid-sentence) is treated as a graceful-overlap behavior rather than a guaranteed real-time interruption feature unless later specified.
- End-of-turn is assumed to be detected via silence/endpointing from the streaming transcription rather than an explicit caller signal.
- The welcome message is a single configurable greeting reused across calls; per-caller personalization is out of scope for this iteration unless later specified.
- "Attended" is assumed to correspond to the call reaching a connected/answered state as reported by Meta's call lifecycle events (User Story 1).
- Conversation turns and call-attended events reuse the platform's existing logging, persistence, and session model rather than introducing a separate store.
