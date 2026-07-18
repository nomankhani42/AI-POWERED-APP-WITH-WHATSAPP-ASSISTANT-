# Contract: TTS Service (48 kHz clean framing, Deepgram Aura active)

> **Current-state amendment (2026-07-11):** Feature 004 originally established clean 48 kHz
> framing with Cartesia. Deepgram Aura now implements the active TTS contract; Cartesia remains
> the rollback function. See
> [the normative TTS contract](../../006-deepgram-tts-enhancement/contracts/tts-service.md).

## Preserved Interface

```python
async def synthesize_stream(
    text_chunks: AsyncIterator[str] | str,
    *,
    call_id: str,
) -> AsyncIterator[SpeechChunk]:
    ...
```

## Preserved Media Invariants

- Output is raw `linear16` mono PCM.
- `SpeechChunk.sample_rate == settings.tts_output_sample_rate`.
- The default sample rate is 48,000 Hz so aiortc can repacketize into fixed 20 ms Opus frames
  without a resample-at-encode boundary.
- Empty input yields no audio and opens no provider connection.
- Whole and incremental text inputs preserve order.
- Provider failures surface as `TtsError`; retry/fallback belongs to the media session.
- Cancellation performs provider cleanup so barge-in does not leave synthesis running.

## Active Deepgram Additions

- Aura model and speed are configurable.
- Speak payloads are capped at 1,800 characters.
- One Flush completes an utterance.
- First-audio and event-gap deadlines prevent silent stalls.
- TTFB and non-sensitive completion telemetry are emitted per `call_id`.

Acceptance coverage lives in `tests/contract/test_deepgram_tts_service.py`; the retained
Cartesia rollback is covered by `tests/contract/test_tts_service.py`.
