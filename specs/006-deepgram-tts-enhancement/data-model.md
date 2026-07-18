# Data Model: Deepgram Aura TTS Enhancement

**Feature**: `006-deepgram-tts-enhancement` | **Date**: 2026-07-11

No persistent database schema changes are required.

## SpeechChunk

| Field | Type | Constraint |
|---|---|---|
| `call_id` | str | Correlates audio with exactly one active call. |
| `audio` | bytes | Non-empty raw `linear16` mono PCM from Deepgram. |
| `sample_rate` | int | Equals `tts_output_sample_rate`; default 48,000 Hz. |

## TTS Settings

| Field | Type | Default | Validation |
|---|---|---:|---|
| `deepgram_api_key` | str | required | Secret; never logged. |
| `deepgram_tts_model` | str | `aura-2-thalia-en` | Provider model identifier. |
| `deepgram_tts_speed` | float | `1.0` | Inclusive `0.7..1.5`. |
| `tts_output_sample_rate` | int | `48000` | Used by TTS and outbound media. |
| `tts_first_audio_timeout_s` | float | `10.0` | Positive. |
| `tts_event_timeout_s` | float | `10.0` | Positive. |

Cartesia settings remain defined because `_synthesize_stream_cartesia` is retained for rollback.

## Transient Stream State

| State | Purpose |
|---|---|
| normalized input iterator | Skips empty pieces while preserving order. |
| text push task | Sends Speak messages concurrently with audio receipt. |
| first-audio deadline | Bounds silence before the first audio bytes. |
| event-gap deadline | Bounds a stalled stream after audio begins. |
| chunk/byte counters | Non-sensitive completion telemetry. |

## Error

`TtsError` remains provider-neutral. The media session catches it and applies its configured
retry and spoken fallback behavior.
