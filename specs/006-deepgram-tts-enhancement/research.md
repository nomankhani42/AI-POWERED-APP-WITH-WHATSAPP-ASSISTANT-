# Research: Deepgram Aura Streaming TTS

**Feature**: `006-deepgram-tts-enhancement` | **Date**: 2026-07-11

## Decision 1: Use Deepgram Speak v1 WebSocket

The installed `deepgram-sdk==7.4.0` exposes
`AsyncDeepgramClient.speak.v1.connect(...)`, `send_text`, `send_flush`, `send_clear`,
`send_close`, and an async response iterator. Audio responses are raw bytes and control
responses include `Flushed` and `Warning`.

**Decision**: Use this SDK surface directly behind the existing provider-neutral interface.

Source: [Deepgram streaming TTS](https://developers.deepgram.com/docs/streaming-text-to-speech)

## Decision 2: Preserve 48 kHz linear PCM

Deepgram supports `linear16` at 48 kHz. Keeping the existing outbound rate avoids a resample
boundary before aiortc packetizes fixed 20 ms Opus frames.

**Decision**: Request raw `linear16` at `tts_output_sample_rate=48000`.

Source: [Deepgram TTS sample rate](https://developers.deepgram.com/docs/tts-sample-rate)

## Decision 3: Bound Speak payload size

Aura accepts at most 2,000 characters in one text payload. LLM replies are usually shorter, but
the service boundary must remain correct for long fixed prompts or generated responses.

**Decision**: Split at 1,800 characters, prefer the last whitespace boundary, and preserve exact
content across chunks.

Source: [Deepgram streaming TTS limits](https://developers.deepgram.com/docs/streaming-text-to-speech)

## Decision 4: Flush exactly once per utterance

Flush forces queued text to audio. Deepgram warns that very frequent flushes can reduce quality
and limits Flush to 20 per 60 seconds.

**Decision**: Push every input piece, then issue one Flush when that utterance's input ends.

Source: [Deepgram TTS Flush](https://developers.deepgram.com/docs/tts-ws-flush)

## Decision 5: Make speed validated and configurable

Aura-2 WebSocket speed control supports `0.7..1.5`, with `1.0` as the natural default.

**Decision**: Add `deepgram_tts_speed` to typed settings with inclusive Pydantic bounds and pass
it during WebSocket connection setup.

Source: [Deepgram TTS voice controls](https://developers.deepgram.com/docs/tts-voice-controls)

## Decision 6: Use punctuation for pacing

Aura derives pause and pacing cues from punctuation. Pronunciation and formatting rules belong in
the agent prompt because the LLM has the semantic context.

**Decision**: Require plain text, complete sentences, deliberate commas/periods, and spoken
grouping for identifiers. Do not rewrite generated text downstream.

Source: [Deepgram Voice Agent TTS controls](https://developers.deepgram.com/docs/voice-agent-tts-controls)

## Decision 7: Add provider deadlines and non-sensitive telemetry

A WebSocket may connect but never produce useful audio. Existing session retries only start after
`TtsError`, so the provider adapter must convert stalls into a bounded failure.

**Decision**: Use a fixed first-audio deadline and a per-event gap deadline, both defaulting to
10 seconds. On failure/cancellation, cancel the producer and send Clear/Close best-effort. Log
TTFB, model, chunk count, and byte count; never log text, audio, or credentials.


## Decision 8: Reuse one WebSocket per call

Deepgram requires conversational agents to use one Speak WebSocket per conversation. Opening a
new socket for each welcome, filler, and reply adds handshake latency and discards stream
continuity.

**Decision**: A `DeepgramTtsSession` is owned by the media call, opened lazily on first speech,
reused across utterances, and closed during call teardown. A failed or interrupted socket is
discarded so the next retry can reconnect cleanly.

Source: [Deepgram streaming TTS](https://developers.deepgram.com/docs/streaming-text-to-speech)

## Decision 9: Stream natural chunks from the live agent response

Deepgram recommends complete sentences for call-center speech and approximately 50-100 character
chunks for low-latency voice assistants. Sending raw LLM tokens harms prosody, while waiting for
the complete answer adds avoidable silence.

**Decision**: Start TTS on the first agent text delta, preserve the exact reply text, and coalesce
deltas at sentence/clause boundaries with a 100-character fallback. Flush once when the agent
response ends.

Source: [Deepgram TTS text chunking](https://developers.deepgram.com/docs/tts-text-chunking)

## Alternatives Considered

- **Remove Cartesia immediately**: rejected; the user explicitly requested a rollback path.
- **Switch the entire app to Deepgram Voice Agent API**: rejected; it would replace the existing
  Meta transport, Agents SDK orchestration, tool flow, and stable service boundaries.
- **Post-process punctuation in TTS**: rejected; downstream rewriting lacks semantic context and
  can corrupt dates, references, or pronunciation-control syntax.
- **Flush after every sentence/token**: rejected; it harms continuity and consumes the provider's
  Flush allowance.
