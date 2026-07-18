# Contract: STT Service (silence-based endpointing + interim signal)

**Module**: `src/app/services/stt.py` (`transcribe_stream`)
**Consumers**: `src/app/services/media/session.py`
**Related**: FR-005, FR-006, FR-011, FR-012; research.md §3

Wraps Deepgram `listen.v1` streaming behind `transcribe_stream(...)`. This contract tunes
endpointing to the clarified ~0.8 s pause and formalizes the interim signal used for barge-in.

## Signature (unchanged shape)

```
async def transcribe_stream(
    audio_chunks: AsyncIterator[bytes], *, call_id: str, sample_rate: int | None = None,
) -> AsyncIterator[TranscriptSegment]
```

## Connection options (new)

The `client.listen.v1.connect(...)` call MUST set:
- `model = settings.deepgram_model` (`nova-3`), `encoding = "linear16"`, `sample_rate = rate`,
  `channels = 1`.
- `interim_results = True`
- `endpointing = settings.stt_endpointing_ms` (default **800**) — ms of silence before a
  segment flips to `speech_final: true`.
- `utterance_end_ms = str(settings.stt_utterance_end_ms)` (default "1000") and
  `vad_events = True` — `UtteranceEnd` backstop.

## Emission rules

- Interim recognition → `TranscriptSegment(is_final=False, text=<partial>)` (non-empty only).
- Endpointed utterance (`speech_final: true`) → `TranscriptSegment(is_final=True, text=<final>)`.
- Silence → **zero** segments, never an error (FR-011).
- The stream stays open across multiple utterances for one call, emitting multiple finals.

## Error semantics
Any connection/send/receive failure or timeout raises `SttError` (FR-011). The session — not
this module — decides retry-once + apology + continue (see agent-turn / session behavior).

**Acceptance**:
- Feeding audio with a ~0.8 s trailing pause yields a final segment (`is_final=True`) once,
  not before the pause.
- A brief mid-sentence pause (< 0.8 s) does NOT produce a premature final (FR-012).
- Non-empty interim segments are emitted before the final (used for barge-in detection).
- Pure silence yields no segments and no exception.
