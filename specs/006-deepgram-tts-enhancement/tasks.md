# Tasks: Deepgram Aura TTS Enhancement

**Feature**: `006-deepgram-tts-enhancement`  
**Status**: Complete

- [x] T001 Verify installed Deepgram SDK async Speak v1 WebSocket methods and response types.
- [x] T002 Make Deepgram Aura the active implementation behind `synthesize_stream`.
- [x] T003 Retain the previous Cartesia implementation as `_synthesize_stream_cartesia`.
- [x] T004 Add `deepgram_tts_model` with default `aura-2-thalia-en`.
- [x] T005 Preserve raw `linear16` output at 48 kHz.
- [x] T006 Add validated `deepgram_tts_speed` configuration.
- [x] T007 Split Speak payloads at 1,800 characters while preserving exact content.
- [x] T008 Send one Flush per completed utterance and stop on `Flushed`.
- [x] T009 Add first-audio and event-gap deadlines.
- [x] T010 Cancel the producer and Clear/Close on interruption or failure.
- [x] T011 Log TTFB, warning details, chunk count, and byte count without content.
- [x] T012 Add voice-friendly punctuation and formatting rules to the agent instructions.
- [x] T013 Add active Deepgram contract tests.
- [x] T014 Keep and retarget Cartesia tests to the rollback function.
- [x] T015 Update typed settings, `.env.example`, README, constitution, and specifications.
- [x] T016 Run focused TTS contracts and the complete pytest suite.
