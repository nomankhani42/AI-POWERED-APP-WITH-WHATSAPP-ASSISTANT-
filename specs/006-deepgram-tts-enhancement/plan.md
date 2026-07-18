# Implementation Plan: Deepgram Aura TTS Enhancement

**Branch**: `006-deepgram-tts-enhancement` | **Date**: 2026-07-11  
**Spec**: [spec.md](./spec.md)

## Summary

Make Deepgram Aura the active provider behind the existing `synthesize_stream` boundary while
retaining Cartesia as dormant rollback code. Enhance the active path with validated speed control,
payload-safe text splitting, provider-stall deadlines, cancellation cleanup, and TTFB telemetry.
Keep the 48 kHz `SpeechChunk` and media-session contracts unchanged.

## Technical Context

- **Language**: Python 3.12
- **Framework**: FastAPI/Uvicorn, fully async
- **Speech SDK**: `deepgram-sdk>=7.4.0`; `cartesia>=3.3.0` retained
- **Active TTS**: Deepgram Aura-2, default `aura-2-thalia-en`
- **Output**: raw `linear16`, mono, 48 kHz
- **Testing**: pytest + pytest-asyncio, SDK mocked at the TTS module boundary

## Constitution Check

| Principle | Result | Evidence |
|---|---|---|
| I. Modular Architecture | PASS | Provider code stays in `services/tts.py` behind the existing interface. |
| II. Async-First | PASS | Async Deepgram client, WebSocket, producer task, and iterator only. |
| III. Layered Memory | N/A | No memory behavior changes. |
| IV. Voice Pipeline Integrity | PASS | Meta → Deepgram STT → Agents SDK → Deepgram Aura TTS; stable `SpeechChunk` boundary retained. |
| V. Configuration | PASS | Model, speed, and deadlines live in typed `Settings`; no secrets logged. |
| VI. Documentation-Driven | PASS | Deepgram Speak v1, Flush, limits, chunking, and voice-control docs were checked before implementation. |

## Design

### Active provider flow

1. Normalize whole or incremental input and skip empty pieces.
2. Lazily construct `AsyncDeepgramClient` only after the first non-empty piece.
3. Open Speak v1 with model, `linear16`, output rate, and speed.
4. Split any piece over 1,800 characters at a whitespace boundary.
5. Push pieces concurrently with response consumption, then send one Flush.
6. Yield non-empty byte events as `SpeechChunk`.
7. Stop on `SpeakV1Flushed`; log warnings and timing metadata.
8. On cancellation/failure, cancel the pusher and attempt Clear/Close.
9. Wrap provider failures in `TtsError`.

### Rollback

The complete Cartesia implementation remains under `_synthesize_stream_cartesia`. It is not
selected by the calling agent, but its dependency, settings, and contract tests remain available.

### Configuration

| Setting | Default | Constraint |
|---|---:|---|
| `deepgram_tts_model` | `aura-2-thalia-en` | Valid Aura model name |
| `deepgram_tts_speed` | `1.0` | `0.7 <= value <= 1.5` |
| `tts_output_sample_rate` | `48000` | Supported Deepgram sample rate |
| `tts_first_audio_timeout_s` | `10.0` | Positive |
| `tts_event_timeout_s` | `10.0` | Positive |

## Files

- `src/app/services/tts.py`: active Deepgram implementation and retained Cartesia rollback.
- `src/app/core/config.py`: validated TTS settings.
- `src/app/agent/assistant.py`: voice-friendly output rules.
- `.env.example`, `README.md`: operator documentation.
- `tests/contract/test_deepgram_tts_service.py`: active-provider contract.
- `tests/contract/test_tts_service.py`: retained Cartesia rollback contract.

## Verification

- Compile modified Python modules.
- Run both TTS contract modules.
- Run the complete pytest suite.
- Perform a live call separately with a real key; automated tests make no provider calls.
