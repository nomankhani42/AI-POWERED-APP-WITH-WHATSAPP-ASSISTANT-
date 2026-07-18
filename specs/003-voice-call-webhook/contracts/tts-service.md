# Contract: Text-to-Speech Service (Deepgram Aura active)

**Module**: `src/app/services/tts.py`  
**Consumer**: `src/app/services/media/session.py`

## Interface

```python
async def synthesize_stream(
    text_chunks: AsyncIterator[str] | str,
    *,
    call_id: str,
) -> AsyncIterator[SpeechChunk]:
    ...
```

The provider is not exposed to consumers. `SpeechChunk` remains
`{call_id: str, audio: bytes, sample_rate: int}`.

## Preconditions

- `call_id` identifies the active call.
- Input may be a whole string or incremental text pieces.
- Empty pieces are ignored. If no non-empty piece exists, no provider client is created.

## Provider Request

- Client: `AsyncDeepgramClient(api_key=settings.deepgram_api_key)`.
- Endpoint: `client.speak.v1.connect`.
- Model: `settings.deepgram_tts_model`.
- Encoding: raw `linear16`.
- Sample rate: `settings.tts_output_sample_rate` (default 48 kHz).
- Speed: `settings.deepgram_tts_speed` (validated `0.7..1.5`).
- Each Speak text payload is at most 1,800 characters.
- Exactly one Flush is sent after all input for the utterance.

## Output

- Each non-empty byte response becomes one ordered `SpeechChunk`.
- Control messages do not become audio.
- `SpeakV1Warning` is logged without failing an otherwise healthy stream.
- `SpeakV1Flushed` completes the utterance.
- First-audio latency is logged with `call_id` and model.
- Reply text, audio bytes, and credentials are never logged.

## Deadlines and Errors

- Before audio: one fixed `tts_first_audio_timeout_s` deadline.
- After audio starts: `tts_event_timeout_s` maximum gap between events.
- Timeout, connection, provider, and push failures surface as `TtsError`.
- Cancellation/failure cancels the producer and attempts Clear and Close.
- Session-level retry and spoken fallback remain the consumer's responsibility.

## Rollback Contract

`_synthesize_stream_cartesia` retains the former Cartesia implementation. It is intentionally
not called by the active calling agent. Its dependency, settings, and mocked tests remain present.

## Acceptance Tests

`tests/contract/test_deepgram_tts_service.py` verifies:

- audio ordering, `call_id`, 48 kHz output, model, encoding, and speed;
- whole and incremental input;
- empty input without provider construction;
- early audio while incremental input is still arriving;
- connection and mid-stream failures;
- exact reconstruction of oversized split input;
- first-audio timeout cleanup.

`tests/contract/test_tts_service.py` verifies the retained Cartesia rollback path.
