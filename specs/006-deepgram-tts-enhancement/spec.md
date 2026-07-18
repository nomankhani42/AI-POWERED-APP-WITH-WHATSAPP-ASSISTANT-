# Feature Specification: Deepgram Aura TTS Enhancement

**Feature Branch**: `006-deepgram-tts-enhancement`  
**Created**: 2026-07-11  
**Status**: Implemented  
**Input**: Switch the calling agent to Deepgram TTS without removing Cartesia, then enhance
the working TTS path.

## Clarifications

- Deepgram Aura is the active calling-agent TTS provider.
- Cartesia remains installed and its complete implementation remains available as an explicit
  rollback path; it is not selected by the public `synthesize_stream` interface.
- The public `SpeechChunk` contract and the 48 kHz WebRTC media path do not change.
- TTS enhancement must not introduce content logging or expose provider credentials.

## User Scenarios & Testing

### User Story 1 - Clear Deepgram speech on every call (Priority: P1)

A caller hears the welcome, fillers, and agent replies synthesized by Deepgram Aura as clear,
continuous 48 kHz audio without any change to the call-session code.

**Independent Test**: Mock Deepgram's Speak v1 WebSocket, pass non-empty text through
`synthesize_stream`, and verify ordered `SpeechChunk` output with the original `call_id`.

**Acceptance Scenarios**:

1. **Given** a complete reply, **When** it is synthesized, **Then** the service sends it to Aura,
   flushes once, and streams every non-empty audio byte response at 48 kHz.
2. **Given** incremental LLM text, **When** pieces arrive, **Then** non-empty pieces are forwarded
   in order without collecting the complete response first.
3. **Given** empty input, **When** synthesis is requested, **Then** no provider connection opens
   and no invalid audio is emitted.

### User Story 2 - Natural and configurable delivery (Priority: P2)

An operator can tune speaking speed while the agent produces short, well-punctuated, plain-text
responses that Aura can pace naturally.

**Independent Test**: Verify the configured speed reaches Deepgram's WebSocket query and that
settings reject values outside Aura's supported range.

**Acceptance Scenarios**:

1. **Given** the default configuration, **Then** Aura speaks at `1.0` speed.
2. **Given** a speed from `0.7` through `1.5`, **Then** that value is used for every request.
3. **Given** an oversized reply, **Then** it is split into ordered Speak messages no longer than
   1,800 characters and reconstructs exactly to the original text.
4. **Given** an agent reply, **Then** it uses plain text, complete sentences, and deliberate
   punctuation rather than Markdown or visual-only formatting.

### User Story 3 - No silent provider stalls (Priority: P1)

A caller is never left waiting indefinitely when Deepgram is unavailable or stops emitting audio.

**Independent Test**: Stall a mocked provider before first audio and between events; verify a
typed `TtsError`, queue clearing, socket closure, and session-level retry/fallback behavior.

**Acceptance Scenarios**:

1. **Given** no first audio within the configured deadline, **Then** synthesis raises `TtsError`
   and closes the provider stream.
2. **Given** audio started but subsequent events stall, **Then** the event-gap timeout applies.
3. **Given** caller barge-in or task cancellation, **Then** pending input is cancelled and
   Deepgram receives Clear and Close best-effort cleanup.
4. **Given** the first audio chunk, **Then** the backend logs TTFB with `call_id` and model but
   never logs reply text, API keys, or raw audio.

## Requirements

### Functional Requirements

- **FR-001**: `synthesize_stream` MUST use `AsyncDeepgramClient.speak.v1.connect` with the
  configured Aura model.
- **FR-002**: The previous Cartesia implementation MUST remain available as
  `_synthesize_stream_cartesia` for rollback.
- **FR-003**: The public TTS interface MUST continue accepting `str | AsyncIterator[str]` and
  yielding `AsyncIterator[SpeechChunk]`.
- **FR-004**: Deepgram output MUST be raw `linear16` mono PCM at
  `tts_output_sample_rate` (default 48,000 Hz).
- **FR-005**: Empty input MUST return without constructing a provider client.
- **FR-006**: Non-empty streamed text pieces MUST be forwarded in order and flushed once when
  input completes.
- **FR-007**: One Deepgram Speak payload MUST NOT exceed 1,800 characters, leaving margin below
  Aura's 2,000-character request limit.
- **FR-008**: Oversized text splitting MUST preserve the exact original content and prefer
  whitespace boundaries.
- **FR-009**: `deepgram_tts_speed` MUST default to `1.0` and validate the inclusive range
  `0.7..1.5`.
- **FR-010**: `tts_first_audio_timeout_s` and `tts_event_timeout_s` MUST be positive,
  configurable values defaulting to 10 seconds.
- **FR-011**: Provider, connection, send, and timeout failures MUST surface as `TtsError`.
- **FR-012**: Cancellation or failure MUST cancel the text-push task and attempt Clear and Close.
- **FR-013**: The service MUST log first-audio latency and a non-sensitive completion summary.
- **FR-014**: Deepgram warning events MUST be logged with `call_id`, warning code, and description.
- **FR-015**: Agent instructions MUST request plain text, complete sentences, deliberate
  punctuation, and clearly grouped dates, prices, phone numbers, and booking references.
- **FR-016**: Cartesia configuration and dependency MUST remain present while rollback is retained.
- **FR-017**: Active Deepgram and retained Cartesia paths MUST have isolated mocked contract tests.

### Key Entities

- **SpeechChunk**: `{call_id: str, audio: bytes, sample_rate: int}`.
- **TTS configuration**: Aura model, speed, output sample rate, first-audio timeout, event timeout.
- **TtsError**: Provider-neutral failure surfaced to the media session for retry/fallback.

## Success Criteria

- **SC-001**: All TTS contract and call-session tests pass with providers mocked.
- **SC-002**: Every emitted Deepgram `SpeechChunk` reports 48,000 Hz by default.
- **SC-003**: No Speak message exceeds 1,800 characters and concatenating split messages exactly
  reproduces the input.
- **SC-004**: A stalled mocked provider fails within the configured deadline and is cleaned up.
- **SC-005**: Existing callers of `synthesize_stream` require no code changes.
- **SC-006**: Cartesia rollback tests remain green.
- **SC-007**: TTFB is observable per call without recording text, credentials, or audio.
