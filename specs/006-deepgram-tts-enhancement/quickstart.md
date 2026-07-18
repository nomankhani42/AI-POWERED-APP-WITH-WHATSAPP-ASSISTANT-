# Quickstart: Validate Deepgram Aura TTS

## Configuration

Required:

```env
DEEPGRAM_API_KEY=<real-key>
```

Optional defaults:

```env
DEEPGRAM_TTS_MODEL=aura-2-thalia-en
DEEPGRAM_TTS_SPEED=1.0
TTS_OUTPUT_SAMPLE_RATE=48000
TTS_FIRST_AUDIO_TIMEOUT_S=10
TTS_EVENT_TIMEOUT_S=10
```

Cartesia variables remain supported only for the retained rollback function.

## Automated Validation

No live provider key is used:

```bash
uv run pytest -q tests/contract/test_deepgram_tts_service.py   tests/contract/test_tts_service.py
uv run pytest -q
```

Expected:

- Deepgram contract tests validate streamed audio, speed, natural sentence chunking, one-socket reuse, failures, and timeouts.
- Cartesia rollback contract tests remain green.
- The complete call-session regression suite passes.

## Manual Live Validation

1. Start the backend:
   ```bash
   uv run uvicorn app.main:app --app-dir src --host 0.0.0.0 --port 8000
   ```
2. Place a WhatsApp call and confirm the welcome, filler, and agent reply all use the selected
   Aura voice. Confirm later replies reuse the same WebSocket and begin playing before the full
   agent answer has finished generating.
3. Confirm logs contain `Deepgram TTS first audio call_id=...` with latency, but no reply text
   or credentials.
4. Set `DEEPGRAM_TTS_SPEED=0.95`, restart, and verify slightly slower speech.
5. Restore `1.0` unless the slower rate is preferred for production.

## Failure Validation

Use mocked tests for deterministic failure validation. Do not deliberately invalidate a
production key during an active call. The timeout contract proves a stalled stream raises
`TtsError`, clears/closes the socket, and allows the media session to retry or apologize.

## Done When

- Automated tests pass.
- A live call has clear 48 kHz playback.
- TTFB is present in logs and contains no sensitive content.
- Speed tuning works within `0.7..1.5`.
