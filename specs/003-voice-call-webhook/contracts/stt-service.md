# Contract: Speech-to-Text Service (Deepgram streaming, chunked)

Module: `src/app/services/stt.py`. Reusable and independently testable (FR-007, FR-010).
Backed by the Deepgram async streaming API; provider is swappable behind this interface (Principle IV).

## Interface

```python
async def transcribe_stream(
    audio_chunks: AsyncIterator[bytes],
    *,
    call_id: str,
    sample_rate: int = 16000,
) -> AsyncIterator[TranscriptSegment]:
    """Consume PCM (linear16) audio chunks; yield transcript segments as they finalize."""
```

`TranscriptSegment = { call_id: str, text: str, is_final: bool, ts: float }`.

## Behavior

- Opens `AsyncDeepgramClient.listen.v2.connect(model=settings.deepgram_model,
  encoding="linear16", sample_rate=sample_rate)` as an async context manager.
- Streams each inbound chunk with `await connection.send_media(chunk)`; registers
  `connection.on(EventType.MESSAGE, ...)` to collect interim + finalized results.
- Uses Deepgram end-of-turn / endpointing events to mark `is_final=True` segments, signalling the
  caller has finished an utterance (drives one `run_turn` invocation).
- On stream end, calls `send_finalize()` then `send_close_stream()` to flush the last utterance.

## Acceptance behaviors (from spec)

| Input | Expected output |
|-------|-----------------|
| Clear caller audio (US2 AS1) | Final segment(s) whose `text` matches the spoken words (SC-006). |
| Silence-only audio (US2 AS2, FR-011) | Empty/flagged result — **no exception**. |
| Provider unavailable / timeout (US2 AS3, FR-009) | Typed `SttError` surfaced so the call degrades gracefully; never hangs. |

## Testing

`tests/contract/test_stt_service.py` mocks the Deepgram connection at the module boundary
(no network), feeds synthetic chunk iterators, and asserts segment stream, silence handling, and
error propagation. No real API key required in tests.
