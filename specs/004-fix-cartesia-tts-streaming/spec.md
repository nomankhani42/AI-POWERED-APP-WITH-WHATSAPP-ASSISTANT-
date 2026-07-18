# Feature Specification: Clear Cartesia Voice Playback & Real-Time Streaming Loop

> **Current-state amendment (2026-07-11):** This document records the original Cartesia-era implementation. Deepgram Aura is now the active TTS provider; Cartesia remains an independently tested rollback path. The current normative design is [006-deepgram-tts-enhancement](../006-deepgram-tts-enhancement/spec.md).

**Feature Branch**: `004-fix-cartesia-tts-streaming`

**Created**: 2026-07-07

**Status**: Draft

**Input**: User description: "make sure the cartesia tts is like old tv like sound toun toush not connecting first of all fix this . when the user speaks after welcome message its listen clear and send to the llm of agent sdk in chunks and agent response in streaming strart tts using cartesia"

## Clarifications

### Session 2026-07-07

- Q: Should the caller be able to interrupt the assistant mid-reply (barge-in)? → A: B — Required this release: the assistant's playback stops the instant the caller starts speaking, and the system immediately switches to capturing the caller.
- Q: What triggers the reasoning agent to respond to a caller turn? → A: A — Silence-based endpointing: audio/transcript chunks stream continuously, and when the caller pauses briefly (~0.7–1s of silence) the turn is finalized and the full transcript is sent to the agent to respond.
- Q: What happens if the caller says nothing after the welcome message? → A: A — Re-prompt once after ~5–8s of silence ("Are you still there?"); if the caller is still silent after another window, end the call gracefully with a closing message.
- Q: How should a mid-call provider failure (transcription/synthesis) be handled from the caller's perspective? → A: A — Retry once silently; if it still fails, play a brief spoken apology ("Sorry, I didn't catch that — could you say it again?") and continue the call rather than dropping it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Caller hears clear, natural spoken replies (Priority: P1)

When the assistant speaks to a caller during a live voice call, the caller hears smooth, natural, intelligible speech from start to finish. Today the spoken audio is broken and distorted — it sounds like a malfunctioning old television: garbled buzzing/static ("toun toush"), stuttering, and playback that never properly "connects" into continuous speech. This must be fixed first, because no other part of the conversation matters if the caller cannot understand what the assistant is saying.

**Why this priority**: Distorted, unintelligible playback makes the entire voice product unusable — the caller cannot understand a single reply. Every other capability (listening, reasoning, streaming) is worthless if the output the caller hears is garbled. Fixing playback quality is the single highest-value outcome and a prerequisite for everything else.

**Independent Test**: Place a live call, trigger any assistant reply, and listen to the full playback. Confirm the audio is continuous, correctly paced, free of static/buzzing/robotic artifacts, and clearly intelligible for the entire utterance — with no dropped, doubled, or corrupted segments.

**Acceptance Scenarios**:

1. **Given** the assistant produces a text reply during a live call, **When** that reply is spoken back to the caller, **Then** the caller hears clear, continuous, natural speech with no static, buzzing, stutter, or gaps.
2. **Given** the welcome message plays when a call connects, **When** the caller listens, **Then** the welcome greeting is fully intelligible and correctly paced from the first word to the last.
3. **Given** a longer multi-sentence reply, **When** it is spoken, **Then** the audio remains continuous across the whole message without cutting out, restarting, or degrading into noise partway through.
4. **Given** the spoken audio is played on the call, **When** its playback characteristics are inspected, **Then** they match the format the call channel expects, so no distortion is introduced by a format or timing mismatch.

---

### User Story 2 - Caller speech is captured clearly and understood (Priority: P2)

After the welcome message finishes, the caller starts speaking. Their speech is captured cleanly in real time, streamed to the assistant's reasoning layer as it arrives (in incremental chunks rather than waiting for the whole utterance), and accurately transcribed so the assistant understands the request.

**Why this priority**: Understanding the caller is the input half of the conversation. It builds on clear playback (P1) and is required before the assistant can respond meaningfully. Streaming speech in chunks — rather than waiting for the caller to finish — is what keeps the exchange feeling live rather than delayed.

**Independent Test**: After the welcome message ends, speak a request into the call. Confirm the audio is captured without clipping or drops, forwarded to the reasoning layer incrementally as the caller talks, and transcribed into text that matches what was said.

**Acceptance Scenarios**:

1. **Given** the welcome message has finished playing, **When** the caller begins speaking, **Then** their speech is captured clearly and the system begins processing it without waiting for a long silence.
2. **Given** the caller is mid-sentence, **When** their audio arrives, **Then** it is forwarded to the assistant's reasoning layer in incremental chunks as it streams, rather than only after the full utterance completes.
3. **Given** the caller finishes a sentence, **When** the captured speech is transcribed, **Then** the resulting text accurately reflects what the caller said.
4. **Given** background noise or a brief pause, **When** the caller speaks, **Then** the system still captures the intended speech and does not treat a natural pause as the end of the conversation.

---

### User Story 3 - Assistant replies begin playing while it is still responding (Priority: P3)

As the assistant forms its answer, its response is produced progressively (streamed) rather than all at once, and spoken playback to the caller begins as soon as the first portion of the answer is ready — instead of waiting for the entire answer to be complete. This makes replies feel fast and conversational.

**Why this priority**: This closes the loop and makes the conversation feel responsive. It depends on clear playback (P1) and captured speech (P2) already working. Starting playback early from a streamed response is what removes the awkward silent wait between the caller finishing and the assistant beginning to speak.

**Independent Test**: Ask the assistant a question that yields a multi-sentence answer and measure the delay from when the caller stops speaking to when the assistant starts speaking. Confirm playback begins from the earliest ready portion of the answer, well before the full answer has been generated, and remains continuous as the rest streams in.

**Acceptance Scenarios**:

1. **Given** the assistant is generating a multi-sentence reply, **When** the first portion of that reply becomes available, **Then** spoken playback to the caller begins from that portion without waiting for the whole reply to finish.
2. **Given** the reply is still being generated, **When** later portions become available, **Then** they are spoken in order and joined onto the earlier audio so the caller hears one continuous, correctly ordered reply.
3. **Given** the assistant has finished speaking a reply, **When** playback ends, **Then** the system returns to listening for the caller so the next listen → respond turn can begin.

---

### Edge Cases

- What happens when the caller speaks over the assistant while it is still talking (barge-in)? The system should stop or yield playback and capture the caller instead of talking over them.
- How does the system handle a reply portion that arrives out of order or is delayed? Later audio must not be played before earlier audio, and a stalled portion must not corrupt or desynchronize the ongoing playback.
- What happens if the speech provider or synthesis provider briefly fails mid-call? The operation is retried once silently; if it still fails, the system plays a brief spoken apology and continues the call rather than emitting garbled audio or leaving the caller in silence.
- How does the system handle a very long reply? Playback must stay continuous without degrading into distortion or exhausting the caller's patience with dead air.
- What happens when the caller is silent for an extended period after the welcome message? The system should not hang indefinitely; it should prompt, wait, or end the turn gracefully.
- What happens if the audio format expected by the call channel and the format produced by synthesis disagree? This mismatch (the suspected root cause of the "old TV" distortion) must be reconciled so no distortion reaches the caller.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST play spoken replies to the caller as clear, continuous, natural speech, free of static, buzzing, stuttering, robotic artifacts, and dropped or doubled segments.
- **FR-002**: The system MUST ensure the audio it plays over the call matches the audio format, sample rate, and encoding the call channel expects, so that no distortion is introduced by a format or timing mismatch.
- **FR-003**: The system MUST play a fully intelligible welcome message automatically when a call connects.
- **FR-004**: After the welcome message finishes, the system MUST capture the caller's speech clearly in real time without clipping, dropping, or corrupting the audio.
- **FR-005**: The system MUST stream captured caller speech to the transcription layer incrementally in chunks as it arrives (rather than buffering the whole utterance), and MUST finalize the caller's turn via silence-based endpointing — detecting a brief pause (~0.7–1 second of silence) — before handing the completed turn to the reasoning agent to respond.
- **FR-006**: The system MUST transcribe captured caller speech into text that accurately reflects what the caller said.
- **FR-007**: The system MUST produce the assistant's reply progressively (streamed) rather than only as a single complete block.
- **FR-008**: The system MUST begin spoken playback of the reply from the earliest ready portion of the streamed response, without waiting for the entire reply to be generated.
- **FR-009**: The system MUST play streamed reply portions in their correct order and join them into one continuous, uninterrupted spoken reply.
- **FR-010**: After a reply finishes playing, the system MUST return to listening for the caller so the listen → respond loop continues for the next turn.
- **FR-011**: The system MUST handle failures in speech capture, transcription, reasoning, or synthesis explicitly: it MUST retry the failed operation once silently, and if the retry also fails, play a brief spoken apology (e.g., "Sorry, I didn't catch that — could you say it again?") and continue the call rather than dropping it. The system MUST NOT emit garbled audio or leave the caller in unexplained silence.
- **FR-012**: The system MUST distinguish a natural short pause in the caller's speech from the actual end of their turn: only a sustained pause (~0.7–1 second of silence) finalizes the turn, so a brief mid-sentence pause does not cut the caller off prematurely.
- **FR-013**: The system MUST allow the caller to interrupt (barge in) while the assistant is speaking: as soon as the caller starts talking, the system MUST stop playback promptly and switch to capturing the caller's speech.
- **FR-014**: When the caller is silent during a listening phase, the system MUST re-prompt once (e.g., "Are you still there?") after roughly 5–8 seconds of silence; if the caller remains silent for a further window, the system MUST end the call gracefully with a closing message rather than hanging indefinitely.

### Key Entities *(include if feature involves data)*

- **Call Session**: A single live voice call with a caller, tracking its lifecycle state (connecting, welcome, listening, thinking, speaking, ended) and the current turn.
- **Caller Utterance**: A stretch of caller speech captured during a listening phase, streamed in incremental audio chunks and associated with a resulting transcript.
- **Assistant Reply**: The assistant's response for a turn, produced as an ordered sequence of streamed text portions, each turned into an ordered sequence of spoken audio segments played back to the caller.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In listening tests of live calls, at least 95% of assistant replies are rated fully intelligible with no perceptible static, buzzing, stutter, or dropout — up from the current broken "old TV" state where playback is unintelligible.
- **SC-002**: 100% of played replies use audio characteristics compatible with the call channel, with zero distortion attributable to a format or sample-rate mismatch.
- **SC-003**: The welcome message plays clearly and completely on 100% of connected calls.
- **SC-004**: Caller speech captured after the welcome message is transcribed with word accuracy of at least 90% on clear speech.
- **SC-005**: The delay from when the caller stops speaking to when the assistant begins speaking is under 1.5 seconds for typical replies, because playback starts from the first ready portion rather than the completed reply.
- **SC-006**: Across a full multi-turn call, the listen → respond loop completes every turn and returns to listening, with no turn left hanging in silence.
- **SC-007**: Caller speech begins being processed by the reasoning layer while the caller is still talking (incrementally), not only after the utterance ends.
- **SC-008**: When the caller barges in while the assistant is speaking, playback stops within 500 ms of the caller's speech being detected, and the system captures the caller's new speech rather than talking over them.

## Assumptions

- The existing voice call webhook, speech-to-text, and text-to-speech integrations from the prior feature (`003-voice-call-webhook`) are in place; this feature fixes and completes their real-time behavior rather than building them from scratch.
- The distorted "old TV / toun toush" playback is caused primarily by a mismatch between the audio format/sample rate/encoding produced for playback and what the call channel expects (or by mis-framed/mis-timed audio chunks); reconciling that mismatch is in scope.
- Callers connect over the existing business voice channel; the caller experience is audio-only (no separate app UI is required for this feature).
- "In chunks" for caller speech means incremental streaming of the caller's audio to the reasoning layer as it arrives, and "streaming" for the reply means progressive generation of the assistant's answer with playback starting on the earliest ready portion.
- Reasonable real-time latency targets (welcome plays on connect; sub-1.5s reply start) are acceptable defaults absent a stricter requirement.
- The assistant's reasoning/answer quality itself is out of scope here; this feature concerns capturing, streaming, and faithfully voicing the conversation, not what the assistant decides to say.
